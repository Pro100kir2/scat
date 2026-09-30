package agent

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Tests talk to loopback servers, which production validation rightly refuses.
func testChecker() *Checker {
	return &Checker{UserAgent: "scat-probe/test", allowed: func(netip.Addr) bool { return true }}
}

const testID = "job_0123456789abcdef0123"

func hostPort(t *testing.T, addr string) (string, int) {
	t.Helper()
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		t.Fatal(err)
	}
	p, _ := strconv.Atoi(port)
	return host, p
}

func TestTCPConnectSuccessAndRefused(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	host, port := hostPort(t, ln.Addr().String())
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			c.Close()
		}
	}()
	job := Job{ID: testID, Type: TCPConnect, Host: host, Port: port, TimeoutMS: 2000}
	res := testChecker().Run(context.Background(), job)
	if res.Outcome != Success || res.LatencyMS == nil || res.JobID != testID {
		t.Fatalf("want success with latency, got %+v", res)
	}
	ln.Close()
	res = testChecker().Run(context.Background(), job)
	if res.Outcome != Failed || res.Error != "connection_refused" {
		t.Fatalf("want refused, got %+v", res)
	}
}

func TestTLSConnect(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()
	host, port := hostPort(t, srv.Listener.Addr().String())
	res := testChecker().Run(context.Background(), Job{ID: testID, Type: TLSConnect, Host: host, Port: port,
		TimeoutMS: 3000, ServerName: "www.example.com"})
	if res.Outcome != Success || !strings.HasPrefix(res.TLSVersion, "TLS 1.") {
		t.Fatalf("want TLS success, got %+v", res)
	}
}

func TestTLSHandshakeTimeout(t *testing.T) {
	// Accepts TCP, then says nothing: what SNI blocking often looks like.
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	defer ln.Close()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			defer c.Close()
		}
	}()
	host, port := hostPort(t, ln.Addr().String())
	start := time.Now()
	res := testChecker().Run(context.Background(), Job{ID: testID, Type: TLSConnect, Host: host, Port: port,
		TimeoutMS: 600, ServerName: "blocked.example.com"})
	if res.Outcome != Timeout {
		t.Fatalf("want timeout, got %+v", res)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("timeout not honoured: %s", elapsed)
	}
}

func TestHTTPGet(t *testing.T) {
	var gotHost, gotUA string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotHost, gotUA = r.Host, r.UserAgent()
		if r.URL.Path == "/redirect" {
			http.Redirect(w, r, "http://10.0.0.1/", http.StatusFound) // must not be followed
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	host, port := hostPort(t, srv.Listener.Addr().String())
	job := Job{ID: testID, Type: HTTPGet, Host: host, Port: port, TimeoutMS: 3000,
		ServerName: "www.gstatic.com", Path: "/generate_204"}
	res := testChecker().Run(context.Background(), job)
	if res.Outcome != Success || res.HTTPStatus != 204 {
		t.Fatalf("want 204, got %+v", res)
	}
	if !strings.HasPrefix(gotHost, "www.gstatic.com") || gotUA != "scat-probe/test" {
		t.Fatalf("Host/UA not as requested: %q %q", gotHost, gotUA)
	}
	job.Path = "/redirect"
	if res := testChecker().Run(context.Background(), job); res.HTTPStatus != http.StatusFound {
		t.Fatalf("redirect was followed: %+v", res)
	}
}

func TestHTTPGetVerifiesCertificates(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()
	host, port := hostPort(t, srv.Listener.Addr().String())
	res := testChecker().Run(context.Background(), Job{ID: testID, Type: HTTPGet, Host: host, Port: port,
		TimeoutMS: 3000, ServerName: "www.gstatic.com", Path: "/", TLS: true})
	if res.Outcome != Failed || res.Error != "tls_certificate" {
		t.Fatalf("a self-signed (captive portal) certificate must fail, got %+v", res)
	}
}

func TestHTTPGetTimeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(2 * time.Second)
	}))
	defer srv.Close()
	host, port := hostPort(t, srv.Listener.Addr().String())
	res := testChecker().Run(context.Background(), Job{ID: testID, Type: HTTPGet, Host: host, Port: port,
		TimeoutMS: 500, ServerName: "slow.example.com", Path: "/"})
	if res.Outcome != Timeout {
		t.Fatalf("want timeout, got %+v", res)
	}
}

func TestInvalidTargetsAreRefusedWithoutConnecting(t *testing.T) {
	production := NewChecker("test")
	valid := Job{ID: testID, Type: TCPConnect, Host: "1.1.1.1", Port: 443, TimeoutMS: 1000}
	cases := map[string]Job{
		"loopback":     {ID: testID, Type: TCPConnect, Host: "127.0.0.1", Port: 443, TimeoutMS: 1000},
		"lan":          {ID: testID, Type: TCPConnect, Host: "192.168.1.1", Port: 80, TimeoutMS: 1000},
		"metadata":     {ID: testID, Type: TCPConnect, Host: "169.254.169.254", Port: 80, TimeoutMS: 1000},
		"cgnat":        {ID: testID, Type: TCPConnect, Host: "100.64.1.1", Port: 443, TimeoutMS: 1000},
		"mapped v6":    {ID: testID, Type: TCPConnect, Host: "::ffff:10.0.0.1", Port: 443, TimeoutMS: 1000},
		"ula":          {ID: testID, Type: TCPConnect, Host: "fd00::1", Port: 443, TimeoutMS: 1000},
		"link-local":   {ID: testID, Type: TCPConnect, Host: "fe80::1", Port: 443, TimeoutMS: 1000},
		"hostname":     {ID: testID, Type: TCPConnect, Host: "router.lan", Port: 443, TimeoutMS: 1000},
		"smtp":         {ID: testID, Type: TCPConnect, Host: "1.1.1.1", Port: 25, TimeoutMS: 1000},
		"timeout":      {ID: testID, Type: TCPConnect, Host: "1.1.1.1", Port: 443, TimeoutMS: 60000},
		"shell":        {ID: testID, Type: "SHELL", Host: "1.1.1.1", Port: 443, TimeoutMS: 1000},
		"tcp with sni": {ID: testID, Type: TCPConnect, Host: "1.1.1.1", Port: 443, TimeoutMS: 1000, ServerName: "x.com"},
		"tls no sni":   {ID: testID, Type: TLSConnect, Host: "1.1.1.1", Port: 443, TimeoutMS: 1000},
		"http abs url": {ID: testID, Type: HTTPGet, Host: "1.1.1.1", Port: 80, TimeoutMS: 1000, ServerName: "x.com", Path: "http://evil/"},
		"http dotdot":  {ID: testID, Type: HTTPGet, Host: "1.1.1.1", Port: 80, TimeoutMS: 1000, ServerName: "x.com", Path: "/a/../b"},
		"bad id":       {ID: "; reboot", Type: TCPConnect, Host: "1.1.1.1", Port: 443, TimeoutMS: 1000},
	}
	if err := valid.Validate(); err != nil {
		t.Fatalf("valid job rejected: %v", err)
	}
	for name, job := range cases {
		if err := job.Validate(); !errors.Is(err, ErrInvalidJob) {
			t.Errorf("%s: want ErrInvalidJob, got %v", name, err)
		}
		if res := production.Run(context.Background(), job); res.Outcome != Error || res.Error != "invalid_job" {
			t.Errorf("%s: want ERROR invalid_job, got %+v", name, res)
		}
	}
}

func TestDecodeJobsRejectsUnknownFields(t *testing.T) {
	jobs, err := DecodeJobs([]byte(`{"jobs":[
		{"job_id":"job_0123456789abcdef0123","type":"TCP_CONNECT","host":"1.1.1.1","port":443,"timeout_ms":1000},
		{"job_id":"job_0123456789abcdef0124","type":"TCP_CONNECT","host":"1.1.1.1","port":443,"timeout_ms":1000,"command":"rm -rf /"}
	]}`))
	if err != nil {
		t.Fatal(err)
	}
	if jobs[0].Validate() != nil || jobs[1].Type != "INVALID" || jobs[1].ID != "job_0123456789abcdef0124" {
		t.Fatalf("unexpected decode: %+v", jobs)
	}
	if _, err := DecodeJobs([]byte(`{"jobs":[],"exec":"id"}`)); err == nil {
		t.Fatal("unknown envelope field accepted")
	}
}

func TestClassify(t *testing.T) {
	cases := map[error]string{
		context.DeadlineExceeded:            Timeout,
		syscall.ECONNREFUSED:                Failed,
		syscall.ECONNRESET:                  Failed,
		syscall.ENETUNREACH:                 Error, // no IPv6 on this probe: not the target's fault
		syscall.EHOSTUNREACH:                Failed,
		&tls.CertificateVerificationError{}: Failed,
	}
	for err, want := range cases {
		if got := classify(err).Outcome; got != want {
			t.Errorf("%v: want %s, got %s", err, want, got)
		}
	}
}
