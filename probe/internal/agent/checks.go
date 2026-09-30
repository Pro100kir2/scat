package agent

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"os"
	"strconv"
	"syscall"
	"time"
)

// maxHTTPBody caps how much of a response is read (and then discarded).
const maxHTTPBody = 64 * 1024

// Checker runs validated jobs. Its zero value is not usable; use NewChecker.
type Checker struct {
	UserAgent string
	allowed   func(netip.Addr) bool // PublicAddr in production
	dialer    net.Dialer
}

func NewChecker(version string) *Checker {
	return &Checker{UserAgent: "scat-probe/" + version, allowed: PublicAddr}
}

// Run executes one job and never panics or returns without a result.
func (c *Checker) Run(ctx context.Context, job Job) Result {
	if err := job.validate(c.allowed); err != nil {
		return Result{JobID: job.ID, Outcome: Error, Error: "invalid_job"}
	}
	timeout := time.Duration(job.TimeoutMS) * time.Millisecond
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	start := time.Now()
	var res Result
	switch job.Type {
	case TCPConnect:
		res = c.tcp(ctx, job)
	case TLSConnect:
		res = c.tls(ctx, job)
	case HTTPGet:
		res = c.http(ctx, job, timeout)
	}
	res.JobID = job.ID
	if res.Outcome == Success {
		ms := float64(time.Since(start).Microseconds()) / 1000
		res.LatencyMS = &ms
	}
	return res
}

// dial connects to the job's IP literal. There is no DNS lookup on the probe at all.
func (c *Checker) dial(ctx context.Context, job Job) (net.Conn, error) {
	return c.dialer.DialContext(ctx, "tcp", job.Addr())
}

func (c *Checker) tcp(ctx context.Context, job Job) Result {
	conn, err := c.dial(ctx, job)
	if err != nil {
		return classify(err)
	}
	_ = conn.Close()
	return Result{Outcome: Success}
}

// tls completes a handshake with the job's SNI. Certificates are not verified on purpose:
// the question is whether the network lets the handshake through (SNI filtering is the
// most common block), not whether we trust the server. Nothing is sent after the handshake.
func (c *Checker) tls(ctx context.Context, job Job) Result {
	conn, err := c.dial(ctx, job)
	if err != nil {
		return classify(err)
	}
	defer conn.Close()
	client := tls.Client(conn, &tls.Config{
		ServerName:         job.ServerName,
		InsecureSkipVerify: true, //nolint:gosec // reachability only; see above
		MinVersion:         tls.VersionTLS12,
		NextProtos:         []string{"h2", "http/1.1"},
	})
	if err := client.HandshakeContext(ctx); err != nil {
		r := classify(err)
		if r.Outcome == Failed && r.Error == "failed" {
			r.Error = "tls_handshake"
		}
		return r
	}
	return Result{Outcome: Success, TLSVersion: tls.VersionName(client.ConnectionState().Version)}
}

// http sends one GET with the job's Host and path. Redirects are not followed, proxies
// from the environment are ignored, and at most 64 KB of the body is read and dropped.
// With TLS, certificates are verified: a captive portal must not look like success.
func (c *Checker) http(ctx context.Context, job Job, timeout time.Duration) Result {
	transport := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return c.dial(ctx, job) // pinned to the job's IP whatever the URL says
		},
		TLSClientConfig:        &tls.Config{ServerName: job.ServerName, MinVersion: tls.VersionTLS12},
		DisableKeepAlives:      true,
		ResponseHeaderTimeout:  timeout,
		MaxResponseHeaderBytes: 16 * 1024,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport:     transport,
		Timeout:       timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	scheme := "http"
	if job.TLS {
		scheme = "https"
	}
	url := scheme + "://" + net.JoinHostPort(job.ServerName, strconv.Itoa(job.Port)) + job.Path
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Result{Outcome: Error, Error: "bad_request"}
	}
	req.Header.Set("User-Agent", c.UserAgent)
	resp, err := client.Do(req)
	if err != nil {
		return classify(err)
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxHTTPBody))
	return Result{Outcome: Success, HTTPStatus: resp.StatusCode}
}

// classify maps network errors to outcomes without leaking free-form text.
func classify(err error) Result {
	var netErr net.Error
	switch {
	case errors.Is(err, context.DeadlineExceeded), errors.Is(err, os.ErrDeadlineExceeded),
		errors.As(err, &netErr) && netErr.Timeout():
		return Result{Outcome: Timeout, Error: "timeout"}
	case errors.Is(err, syscall.ECONNREFUSED):
		return Result{Outcome: Failed, Error: "connection_refused"}
	case errors.Is(err, syscall.ECONNRESET), errors.Is(err, syscall.EPIPE), errors.Is(err, io.EOF):
		return Result{Outcome: Failed, Error: "connection_reset"}
	case errors.Is(err, syscall.EHOSTUNREACH):
		return Result{Outcome: Failed, Error: "host_unreachable"}
	case errors.Is(err, syscall.ENETUNREACH), errors.Is(err, syscall.EADDRNOTAVAIL), errors.Is(err, syscall.EAFNOSUPPORT):
		// The probe has no route of this kind (typically no IPv6): says nothing about the target.
		return Result{Outcome: Error, Error: "network_unreachable"}
	case errors.Is(err, context.Canceled):
		return Result{Outcome: Error, Error: "cancelled"}
	}
	var certErr *tls.CertificateVerificationError
	if errors.As(err, &certErr) {
		return Result{Outcome: Failed, Error: "tls_certificate"}
	}
	return Result{Outcome: Failed, Error: "failed"}
}
