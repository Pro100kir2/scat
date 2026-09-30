package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

const maxResponse = 1 << 20

var (
	ErrUnauthorized = errors.New("credentials rejected by the server")
	// 403: a disabled probe on authenticated calls, a refused token on registration.
	ErrForbidden = errors.New("forbidden by the server")
	ErrRejected  = errors.New("request rejected by the server")
)

// ServerError is a 5xx or 429 reply: worth retrying later.
type ServerError struct {
	Status     int
	RetryAfter time.Duration
}

func (e *ServerError) Error() string { return fmt.Sprintf("server replied %d", e.Status) }

// Credentials identify one probe. The secret is only ever sent to the configured API.
type Credentials struct {
	APIURL  string `json:"api_url"`
	ProbeID string `json:"probe_id"`
	Secret  string `json:"secret"`
	Profile string `json:"profile,omitempty"`
}

// ValidateAPIURL accepts https URLs, and plain http only for local development.
func ValidateAPIURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimRight(raw, "/"))
	if err != nil || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" {
		return "", fmt.Errorf("invalid API URL %q", raw)
	}
	host := u.Hostname()
	local := host == "localhost" || host == "127.0.0.1" || host == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return "", fmt.Errorf("API URL must use https: %q", raw)
	}
	return u.Scheme + "://" + u.Host, nil
}

// Client speaks the probe API. Every request is outbound HTTPS initiated by the probe.
type Client struct {
	BaseURL string
	Version string
	HTTP    *http.Client

	mu    sync.RWMutex
	creds Credentials
}

func NewClient(creds Credentials, version string) *Client {
	return &Client{
		BaseURL: creds.APIURL,
		Version: version,
		HTTP: &http.Client{
			// Long polls last up to 25 s; leave room for slow uplinks.
			Timeout:   45 * time.Second,
			Transport: &http.Transport{Proxy: nil, ForceAttemptHTTP2: true, MaxIdleConns: 2, IdleConnTimeout: 90 * time.Second},
		},
		creds: creds,
	}
}

func (c *Client) Credentials() Credentials {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.creds
}

func (c *Client) setCredentials(creds Credentials) {
	c.mu.Lock()
	c.creds = creds
	c.mu.Unlock()
}

func (c *Client) do(ctx context.Context, method, path string, auth bool, body, out any) error {
	var payload io.Reader
	var length int
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		payload, length = bytes.NewReader(data), len(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, payload)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "scat-probe/"+c.Version)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
		req.ContentLength = int64(length)
	}
	if auth {
		creds := c.Credentials()
		req.Header.Set("Authorization", "Bearer "+creds.ProbeID+"."+creds.Secret)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxResponse))
	if err != nil {
		return err
	}
	switch {
	case resp.StatusCode == http.StatusUnauthorized:
		return ErrUnauthorized
	case resp.StatusCode == http.StatusForbidden:
		return fmt.Errorf("%w: %s", ErrForbidden, detail(data))
	case resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500:
		retry, _ := strconv.Atoi(resp.Header.Get("Retry-After"))
		return &ServerError{Status: resp.StatusCode, RetryAfter: time.Duration(retry) * time.Second}
	case resp.StatusCode >= 400:
		return fmt.Errorf("%w (%d): %s", ErrRejected, resp.StatusCode, detail(data))
	}
	if out != nil {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("unexpected reply to %s: %w", path, err)
		}
	}
	return nil
}

// detail turns the server's `detail` (a string, or FastAPI's field errors) into one short line.
func detail(data []byte) string {
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	if json.Unmarshal(data, &body) != nil || len(body.Detail) == 0 {
		return "no details"
	}
	var text string
	var fields []struct {
		Loc []any  `json:"loc"`
		Msg string `json:"msg"`
	}
	switch {
	case json.Unmarshal(body.Detail, &text) == nil:
	case json.Unmarshal(body.Detail, &fields) == nil:
		parts := make([]string, 0, len(fields))
		for _, f := range fields {
			field := "request"
			if len(f.Loc) > 0 {
				field = fmt.Sprint(f.Loc[len(f.Loc)-1])
			}
			parts = append(parts, field+": "+f.Msg)
		}
		text = strings.Join(parts, "; ")
	default:
		text = string(body.Detail)
	}
	if len(text) > 200 {
		text = text[:200]
	}
	return text
}

// Register trades a one-time enrollment token for a probe ID and secret.
func (c *Client) Register(ctx context.Context, token, profile, platform string) (Credentials, error) {
	var reply struct {
		ProbeID string `json:"probe_id"`
		Secret  string `json:"secret"`
	}
	body := map[string]string{"token": token, "agent_version": c.Version, "profile": profile, "platform": platform}
	if err := c.do(ctx, http.MethodPost, "/api/probes/register", false, body, &reply); err != nil {
		return Credentials{}, err
	}
	if reply.ProbeID == "" || reply.Secret == "" {
		return Credentials{}, errors.New("registration reply has no credentials")
	}
	creds := Credentials{APIURL: c.BaseURL, ProbeID: reply.ProbeID, Secret: reply.Secret, Profile: profile}
	c.setCredentials(creds)
	return creds, nil
}

// HeartbeatReply is what the server says about us.
type HeartbeatReply struct {
	Status            string  `json:"status"`
	HeartbeatSeconds  int     `json:"heartbeat_seconds"`
	RotateCredentials bool    `json:"rotate_credentials"`
	ServerTime        float64 `json:"server_time"`
}

func (c *Client) Heartbeat(ctx context.Context, uptime time.Duration, running int) (HeartbeatReply, error) {
	var reply HeartbeatReply
	body := map[string]any{"agent_version": c.Version, "uptime_seconds": int(uptime.Seconds()), "jobs_running": running}
	err := c.do(ctx, http.MethodPost, "/api/probes/heartbeat", true, body, &reply)
	return reply, err
}

// Jobs long-polls for work for up to `wait`.
func (c *Client) Jobs(ctx context.Context, wait time.Duration, limit int) ([]Job, error) {
	var raw json.RawMessage
	path := fmt.Sprintf("/api/probes/jobs?wait=%d&limit=%d", int(wait.Seconds()), limit)
	if err := c.do(ctx, http.MethodGet, path, true, nil, &raw); err != nil {
		return nil, err
	}
	return DecodeJobs(raw)
}

func (c *Client) Results(ctx context.Context, results []Result) error {
	for start := 0; start < len(results); start += 50 {
		end := min(start+50, len(results))
		if err := c.do(ctx, http.MethodPost, "/api/probes/results", true, map[string]any{"results": results[start:end]}, nil); err != nil {
			return err
		}
	}
	return nil
}

// Rotate asks for a new secret. The caller must persist it before using it.
func (c *Client) Rotate(ctx context.Context) (Credentials, error) {
	var reply struct {
		ProbeID string `json:"probe_id"`
		Secret  string `json:"secret"`
	}
	if err := c.do(ctx, http.MethodPost, "/api/probes/rotate", true, nil, &reply); err != nil {
		return Credentials{}, err
	}
	creds := c.Credentials()
	if reply.ProbeID != creds.ProbeID || reply.Secret == "" {
		return Credentials{}, errors.New("unexpected rotation reply")
	}
	creds.Secret = reply.Secret
	return creds, nil
}
