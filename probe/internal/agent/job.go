// Package agent is the Скат provider probe: it asks the backend for typed network checks,
// runs them, and reports the results. It never runs VLESS or Xray, never executes
// commands, and never listens on a port: every connection it makes is outbound.
package agent

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"regexp"
	"strconv"
)

// The only kinds of work a probe can be given. There is no "command" type.
const (
	TCPConnect = "TCP_CONNECT"
	TLSConnect = "TLS_CONNECT"
	HTTPGet    = "HTTP_GET"
)

const (
	MinTimeoutMS = 500
	MaxTimeoutMS = 10_000
	maxPathLen   = 256
)

// Outcomes reported to the backend.
const (
	Success = "SUCCESS" // the endpoint answered
	Failed  = "FAILED"  // refused, reset, TLS/HTTP error: the network said no
	Timeout = "TIMEOUT" // no answer in time
	Error   = "ERROR"   // the probe could not run the check (its own network, bad job)
)

// Job is one network check. Unknown fields are rejected when decoding.
type Job struct {
	ID         string `json:"job_id"`
	Type       string `json:"type"`
	Host       string `json:"host"`
	Port       int    `json:"port"`
	TimeoutMS  int    `json:"timeout_ms"`
	ServerName string `json:"server_name,omitempty"`
	Path       string `json:"path,omitempty"`
	TLS        bool   `json:"tls,omitempty"`
}

// Result is what goes back. It carries no data from the target beyond status codes.
type Result struct {
	JobID      string   `json:"job_id"`
	Outcome    string   `json:"outcome"`
	LatencyMS  *float64 `json:"latency_ms,omitempty"`
	Error      string   `json:"error,omitempty"`
	HTTPStatus int      `json:"http_status,omitempty"`
	TLSVersion string   `json:"tls_version,omitempty"`
}

var (
	jobIDRe    = regexp.MustCompile(`^job_[0-9a-f]{20}$`)
	hostnameRe = regexp.MustCompile(`^(?i)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)
	pathRe     = regexp.MustCompile(`^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$`)

	// Abuse magnets: never probed, whatever the backend says.
	blockedPorts = map[int]bool{0: true, 25: true, 135: true, 136: true, 137: true, 138: true, 139: true,
		445: true, 465: true, 587: true}

	// Address space a probe must never reach: its own LAN, the device itself, metadata
	// services, carrier NAT, documentation and reserved ranges.
	blockedPrefixes = mustPrefixes(
		"0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
		"192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
		"198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
		"::/128", "::1/128", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32",
		"2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
	)
)

func mustPrefixes(cidrs ...string) []netip.Prefix {
	out := make([]netip.Prefix, len(cidrs))
	for i, c := range cidrs {
		out[i] = netip.MustParsePrefix(c)
	}
	return out
}

// ErrInvalidJob means the job does not have one of the allowed shapes.
var ErrInvalidJob = errors.New("invalid job")

func invalid(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrInvalidJob, fmt.Sprintf(format, args...))
}

// PublicAddr reports whether a probe may connect to ip.
func PublicAddr(ip netip.Addr) bool {
	if !ip.IsValid() || ip.Zone() != "" {
		return false
	}
	ip = ip.Unmap()
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, p := range blockedPrefixes {
		if p.Contains(ip) {
			return false
		}
	}
	return true
}

// DecodeJobs parses the backend's job list strictly: unknown fields are an error.
func DecodeJobs(data []byte) ([]Job, error) {
	var envelope struct {
		Jobs []json.RawMessage `json:"jobs"`
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&envelope); err != nil {
		return nil, fmt.Errorf("decode jobs: %w", err)
	}
	jobs := make([]Job, 0, len(envelope.Jobs))
	for _, raw := range envelope.Jobs {
		var job Job
		d := json.NewDecoder(bytes.NewReader(raw))
		d.DisallowUnknownFields()
		if err := d.Decode(&job); err != nil {
			// Keep the ID if we can, so the backend learns the job was refused.
			var id struct {
				ID string `json:"job_id"`
			}
			_ = json.Unmarshal(raw, &id)
			job = Job{ID: id.ID, Type: "INVALID"}
		}
		jobs = append(jobs, job)
	}
	return jobs, nil
}

// Validate checks the job against the allowed shapes. The backend validates too; this is
// the probe's own line of defence, so a compromised backend still cannot aim it at a LAN.
func (j Job) Validate() error { return j.validate(PublicAddr) }

// validate takes the address policy as a parameter only so tests can use loopback servers.
func (j Job) validate(allowed func(netip.Addr) bool) error {
	if !jobIDRe.MatchString(j.ID) {
		return invalid("bad job id")
	}
	switch j.Type {
	case TCPConnect, TLSConnect, HTTPGet:
	default:
		return invalid("unsupported type %q", j.Type)
	}
	ip, err := netip.ParseAddr(j.Host)
	if err != nil {
		return invalid("host must be an IP literal")
	}
	if !allowed(ip) {
		return invalid("non-public address %s", ip)
	}
	if j.Port < 1 || j.Port > 65535 || blockedPorts[j.Port] {
		return invalid("port %d not allowed", j.Port)
	}
	if j.TimeoutMS < MinTimeoutMS || j.TimeoutMS > MaxTimeoutMS {
		return invalid("timeout %dms out of range", j.TimeoutMS)
	}
	if j.ServerName != "" {
		if len(j.ServerName) > 253 || !hostnameRe.MatchString(j.ServerName) {
			return invalid("bad server name")
		}
		if _, err := netip.ParseAddr(j.ServerName); err == nil {
			return invalid("server name is an IP")
		}
	}
	switch j.Type {
	case TCPConnect:
		if j.ServerName != "" || j.Path != "" || j.TLS {
			return invalid("TCP_CONNECT takes only host and port")
		}
	case TLSConnect:
		if j.ServerName == "" || j.Path != "" {
			return invalid("TLS_CONNECT needs a server name and no path")
		}
	case HTTPGet:
		if j.ServerName == "" || len(j.Path) > maxPathLen || !pathRe.MatchString(j.Path) ||
			bytes.Contains([]byte(j.Path), []byte("..")) || bytes.Contains([]byte(j.Path), []byte("//")) {
			return invalid("HTTP_GET needs a Host name and a plain path")
		}
	}
	return nil
}

// Addr is host:port with IPv6 brackets.
func (j Job) Addr() string {
	ip, _ := netip.ParseAddr(j.Host)
	return netip.AddrPortFrom(ip, uint16(j.Port)).String()
}

func (j Job) String() string {
	return j.Type + " " + j.Host + ":" + strconv.Itoa(j.Port)
}
