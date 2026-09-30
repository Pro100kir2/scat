//go:build integration

// Distributed integration test: many real agents (this package's Agent, Client and Checker,
// unchanged) against a running Скат API. Driven by tests/integration/test_go_agents.py, which
// starts the API, enrolls the probes and checks the outcome; this side only runs the agents.
//
// Two things differ from production, exactly as in the unit tests: loopback targets are
// allowed (the test's endpoints live on 127.0.0.1), and each agent dials through a simulated
// ISP that resets connections to the ports its provider blocks. Every other connection is a
// real TCP/TLS/HTTP connection made by the production checker code.
//
//	SCAT_IT_API   http://127.0.0.1:PORT
//	SCAT_IT_PLAN  JSON file: [{"token": "...", "public_ip": "198.51.0.10", "blocked_ports": [1234]}]
//	SCAT_IT_SECONDS  how long to run (the driver stops the process earlier when done)
package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/netip"
	"os"
	"strconv"
	"sync"
	"syscall"
	"testing"
	"time"
)

type itProbe struct {
	Token        string `json:"token"`
	PublicIP     string `json:"public_ip"`
	BlockedPorts []int  `json:"blocked_ports"`
}

// forwardedFor plays the reverse proxy in front of the API: it tells the API this device's IP.
type forwardedFor struct {
	ip   string
	next http.RoundTripper
}

func (f forwardedFor) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	r.Header.Set("X-Forwarded-For", f.ip)
	return f.next.RoundTrip(r)
}

func TestIntegrationAgents(t *testing.T) {
	api := os.Getenv("SCAT_IT_API")
	planFile := os.Getenv("SCAT_IT_PLAN")
	if api == "" || planFile == "" {
		t.Skip("SCAT_IT_API / SCAT_IT_PLAN not set: run through tests/integration/test_go_agents.py")
	}
	data, err := os.ReadFile(planFile)
	if err != nil {
		t.Fatal(err)
	}
	var plan []itProbe
	if err := json.Unmarshal(data, &plan); err != nil {
		t.Fatal(err)
	}
	seconds, _ := strconv.Atoi(os.Getenv("SCAT_IT_SECONDS"))
	if seconds <= 0 {
		seconds = 120
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(seconds)*time.Second)
	defer cancel()

	var wg sync.WaitGroup
	for i, p := range plan {
		client := NewClient(Credentials{APIURL: api}, "1.0.0")
		client.HTTP.Transport = forwardedFor{ip: p.PublicIP, next: client.HTTP.Transport}
		creds, err := client.Register(ctx, p.Token, "linux-amd64", "linux/amd64")
		if err != nil {
			t.Fatalf("probe %d: register: %v", i, err)
		}
		fmt.Printf("REGISTERED %d %s\n", i, creds.ProbeID)

		checker := NewChecker("1.0.0")
		checker.allowed = func(ip netip.Addr) bool { return ip.IsLoopback() || PublicAddr(ip) }
		blocked := map[int]bool{}
		for _, port := range p.BlockedPorts {
			blocked[port] = true
		}
		checker.dialer.Control = func(_, address string, _ syscall.RawConn) error {
			_, portText, _ := net.SplitHostPort(address)
			port, _ := strconv.Atoi(portText)
			if blocked[port] {
				return syscall.ECONNREFUSED // the ISP resets it, as DPI does
			}
			return nil
		}
		a := &Agent{
			Client: client, Checker: checker, Concurrency: 4,
			PollWait: 2 * time.Second, MinBackoff: 200 * time.Millisecond, MaxBackoff: 2 * time.Second,
			Log: log.New(io.Discard, "", 0),
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = a.Run(ctx)
		}()
	}
	fmt.Println("RUNNING")
	wg.Wait()
}
