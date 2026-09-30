package agent

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeBackend implements the probe API well enough to drive the agent.
type fakeBackend struct {
	mu          sync.Mutex
	secret      string
	failFirst   int32 // answer 503 to this many requests first (reconnect test)
	requests    atomic.Int32
	heartbeats  atomic.Int32
	rotate      atomic.Bool
	jobs        []map[string]any
	results     []Result
	resultsSeen chan struct{}
	authHeaders []string
}

func newBackend(secret string) *fakeBackend {
	return &fakeBackend{secret: secret, resultsSeen: make(chan struct{}, 10)}
}

func (b *fakeBackend) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	n := b.requests.Add(1)
	if n <= b.failFirst {
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	b.mu.Lock()
	secret := b.secret
	b.authHeaders = append(b.authHeaders, r.Header.Get("Authorization"))
	b.mu.Unlock()
	if r.Header.Get("Authorization") != "Bearer prb_0123456789abcdef."+secret {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	switch r.URL.Path {
	case "/api/probes/heartbeat":
		b.heartbeats.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "approved", "heartbeat_seconds": 5,
			"rotate_credentials": b.rotate.Load()})
	case "/api/probes/rotate":
		b.rotate.Store(false)
		b.mu.Lock()
		b.secret = "rotated-secret"
		b.mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]string{"probe_id": "prb_0123456789abcdef", "secret": "rotated-secret"})
	case "/api/probes/jobs":
		b.mu.Lock()
		jobs := b.jobs
		b.jobs = nil
		b.mu.Unlock()
		if jobs == nil {
			jobs = []map[string]any{}
			time.Sleep(20 * time.Millisecond) // a short long-poll
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"jobs": jobs})
	case "/api/probes/results":
		var body struct {
			Results []Result `json:"results"`
		}
		data, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(data, &body)
		b.mu.Lock()
		b.results = append(b.results, body.Results...)
		b.mu.Unlock()
		b.resultsSeen <- struct{}{}
		_, _ = w.Write([]byte(`{"accepted":1,"rejected":[]}`))
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func testAgent(url, secret, configPath string) *Agent {
	creds := Credentials{APIURL: url, ProbeID: "prb_0123456789abcdef", Secret: secret}
	return &Agent{
		Client:     NewClient(creds, "1.0.0"),
		Checker:    testChecker(),
		ConfigPath: configPath,
		Log:        log.New(io.Discard, "", 0),
		PollWait:   50 * time.Millisecond, MinBackoff: 10 * time.Millisecond, MaxBackoff: 50 * time.Millisecond,
		AuthRetry: time.Hour, DisabledRetry: time.Hour,
	}
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func TestHeartbeatAndJobRoundTrip(t *testing.T) {
	target, _ := net.Listen("tcp", "127.0.0.1:0")
	defer target.Close()
	go func() {
		for {
			c, err := target.Accept()
			if err != nil {
				return
			}
			c.Close()
		}
	}()
	host, port := hostPort(t, target.Addr().String())

	backend := newBackend("s3cret")
	backend.jobs = []map[string]any{
		{"job_id": "job_00000000000000000001", "type": "TCP_CONNECT", "host": host, "port": port, "timeout_ms": 1000},
		{"job_id": "job_00000000000000000002", "type": "TCP_CONNECT", "host": host, "port": 1, "timeout_ms": 1000},
	}
	srv := httptest.NewServer(backend)
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := testAgent(srv.URL, "s3cret", "")
	go func() { _ = a.Run(ctx) }()

	select {
	case <-backend.resultsSeen:
	case <-time.After(5 * time.Second):
		t.Fatal("no results delivered")
	}
	eventually(t, "a heartbeat", func() bool { return a.Heartbeats() >= 1 })
	backend.mu.Lock()
	defer backend.mu.Unlock()
	got := map[string]string{}
	for _, r := range backend.results {
		got[r.JobID] = r.Outcome
	}
	if got["job_00000000000000000001"] != Success || got["job_00000000000000000002"] != Failed {
		t.Fatalf("unexpected results: %+v", backend.results)
	}
}

func TestCredentialFailureDoesNotSpin(t *testing.T) {
	backend := newBackend("the-real-secret")
	srv := httptest.NewServer(backend)
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	defer cancel()
	a := testAgent(srv.URL, "wrong-secret", "")
	_ = a.Run(ctx)
	// One heartbeat and one job poll, then both loops wait AuthRetry (an hour).
	if n := backend.requests.Load(); n > 2 {
		t.Fatalf("agent hammered the server after 401: %d requests", n)
	}
	if a.Heartbeats() != 0 {
		t.Fatal("a rejected heartbeat was counted")
	}
}

func TestReconnectAfterOutage(t *testing.T) {
	backend := newBackend("s3cret")
	backend.failFirst = 6 // the server is down for a while
	srv := httptest.NewServer(backend)
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := testAgent(srv.URL, "s3cret", "")
	go func() { _ = a.Run(ctx) }()
	eventually(t, "a heartbeat after the outage", func() bool { return a.Heartbeats() >= 1 })
}

func TestReconnectWhenServerComesBack(t *testing.T) {
	// Nothing listens at first: connection refused, then a server appears on that port.
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := ln.Addr().String()
	ln.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := testAgent("http://"+addr, "s3cret", "")
	go func() { _ = a.Run(ctx) }()
	time.Sleep(100 * time.Millisecond)

	backend := newBackend("s3cret")
	var ln2 net.Listener
	eventually(t, "the port to be free", func() bool {
		var err error
		ln2, err = net.Listen("tcp", addr)
		return err == nil
	})
	srv := &httptest.Server{Listener: ln2, Config: &http.Server{Handler: backend}}
	srv.Start()
	defer srv.Close()
	eventually(t, "reconnection", func() bool { return a.Heartbeats() >= 1 })
}

func TestCredentialRotationIsPersisted(t *testing.T) {
	backend := newBackend("s3cret")
	backend.rotate.Store(true)
	srv := httptest.NewServer(backend)
	defer srv.Close()
	config := filepath.Join(t.TempDir(), "probe.json")
	if err := SaveCredentials(config, Credentials{APIURL: srv.URL, ProbeID: "prb_0123456789abcdef", Secret: "s3cret"}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := testAgent(srv.URL, "s3cret", config)
	go func() { _ = a.Run(ctx) }()
	eventually(t, "rotation", func() bool { return a.Client.Credentials().Secret == "rotated-secret" })
	saved, err := LoadCredentials(config)
	if err != nil || saved.Secret != "rotated-secret" {
		t.Fatalf("rotated secret not saved: %+v %v", saved, err)
	}
	eventually(t, "requests with the new secret", func() bool {
		backend.mu.Lock()
		defer backend.mu.Unlock()
		last := backend.authHeaders[len(backend.authHeaders)-1]
		return last == "Bearer prb_0123456789abcdef.rotated-secret"
	})
}

func TestHostileJobsGetInvalidResults(t *testing.T) {
	backend := newBackend("s3cret")
	backend.jobs = []map[string]any{
		{"job_id": "job_00000000000000000003", "type": "TCP_CONNECT", "host": "192.168.1.1", "port": 80, "timeout_ms": 1000},
		{"job_id": "job_00000000000000000004", "type": "EXEC", "host": "1.1.1.1", "port": 443, "timeout_ms": 1000},
		{"job_id": "job_00000000000000000005", "type": "TCP_CONNECT", "host": "1.1.1.1", "port": 443, "timeout_ms": 1000, "cmd": "id"},
	}
	srv := httptest.NewServer(backend)
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := testAgent(srv.URL, "s3cret", "")
	a.Checker = NewChecker("1.0.0") // production policy
	go func() { _ = a.Run(ctx) }()
	select {
	case <-backend.resultsSeen:
	case <-time.After(5 * time.Second):
		t.Fatal("no results")
	}
	backend.mu.Lock()
	defer backend.mu.Unlock()
	if len(backend.results) != 3 {
		t.Fatalf("want 3 results, got %+v", backend.results)
	}
	for _, r := range backend.results {
		if r.Outcome != Error || r.Error != "invalid_job" {
			t.Errorf("hostile job was run: %+v", r)
		}
	}
}

func TestSaveCredentialsIsPrivate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "probe.json")
	if err := SaveCredentials(path, Credentials{APIURL: "https://api.example.org", ProbeID: "prb_1", Secret: "x"}); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("credentials file mode %v, err %v", info.Mode().Perm(), err)
	}
}

func TestValidateAPIURL(t *testing.T) {
	good := map[string]string{
		"https://api.scat-search.ru/": "https://api.scat-search.ru",
		"http://127.0.0.1:8000":       "http://127.0.0.1:8000",
	}
	for in, want := range good {
		if got, err := ValidateAPIURL(in); err != nil || got != want {
			t.Errorf("%s: got %q %v", in, got, err)
		}
	}
	for _, bad := range []string{"http://api.scat-search.ru", "https://user:pw@host", "ftp://x", "https://x/path", ""} {
		if _, err := ValidateAPIURL(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}
