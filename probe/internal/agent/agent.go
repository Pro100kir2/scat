package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math/rand/v2"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// LoadCredentials reads the probe's config file (written by `scat-probe enroll`).
func LoadCredentials(path string) (Credentials, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Credentials{}, err
	}
	var creds Credentials
	if err := json.Unmarshal(data, &creds); err != nil {
		return Credentials{}, fmt.Errorf("%s: %w", path, err)
	}
	if creds.APIURL, err = ValidateAPIURL(creds.APIURL); err != nil {
		return Credentials{}, err
	}
	if creds.ProbeID == "" || creds.Secret == "" {
		return Credentials{}, fmt.Errorf("%s: no probe credentials", path)
	}
	return creds, nil
}

// SaveCredentials writes atomically with mode 0600, so a crash never leaves half a secret.
func SaveCredentials(path string, creds Credentials) error {
	data, err := json.MarshalIndent(creds, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".probe-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(append(data, '\n')); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// Agent ties it together: heartbeats, job polling, checks, results, credential rotation.
type Agent struct {
	Client      *Client
	Checker     *Checker
	ConfigPath  string // where rotated credentials are saved; empty = keep in memory
	Log         *log.Logger
	Concurrency int

	// Pacing; the defaults suit production, tests shorten them.
	PollWait      time.Duration
	MinBackoff    time.Duration
	MaxBackoff    time.Duration
	AuthRetry     time.Duration // after 401: the secret is wrong, don't hammer the server
	DisabledRetry time.Duration // after 403: an admin disabled this probe

	heartbeatEvery atomic.Int64
	running        atomic.Int32
	started        time.Time
	heartbeats     atomic.Int64
}

func (a *Agent) defaults() {
	if a.Concurrency <= 0 {
		a.Concurrency = 4
	}
	if a.PollWait == 0 {
		a.PollWait = 25 * time.Second
	}
	if a.MinBackoff == 0 {
		a.MinBackoff = 2 * time.Second
	}
	if a.MaxBackoff == 0 {
		a.MaxBackoff = 5 * time.Minute
	}
	if a.AuthRetry == 0 {
		a.AuthRetry = 10 * time.Minute
	}
	if a.DisabledRetry == 0 {
		a.DisabledRetry = time.Hour
	}
	if a.Log == nil {
		a.Log = log.New(os.Stderr, "scat-probe: ", log.LstdFlags)
	}
	if a.heartbeatEvery.Load() == 0 {
		a.heartbeatEvery.Store(int64(30 * time.Second))
	}
}

// Heartbeats reports how many heartbeats succeeded (for tests and `check`).
func (a *Agent) Heartbeats() int64 { return a.heartbeats.Load() }

// Run blocks until ctx is cancelled.
func (a *Agent) Run(ctx context.Context) error {
	a.defaults()
	a.started = time.Now()
	a.Log.Printf("probe %s starting (%s)", a.Client.Credentials().ProbeID, a.Client.Version)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); a.heartbeatLoop(ctx) }()
	go func() { defer wg.Done(); a.jobLoop(ctx) }()
	wg.Wait()
	a.Log.Printf("stopped")
	return ctx.Err()
}

// wait sleeps for d or until ctx ends; false means stop.
func wait(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// pause decides how long to back off after err, with jitter, and whether to log it.
func (a *Agent) pause(err error, failures int) time.Duration {
	var serverErr *ServerError
	switch {
	case errors.Is(err, ErrUnauthorized):
		a.Log.Printf("credentials rejected; re-enroll the probe if this persists (retry in %s)", a.AuthRetry)
		return a.AuthRetry
	case errors.Is(err, ErrForbidden):
		a.Log.Printf("this probe is disabled on the server (retry in %s)", a.DisabledRetry)
		return a.DisabledRetry
	case errors.As(err, &serverErr) && serverErr.RetryAfter > 0:
		return serverErr.RetryAfter
	}
	d := a.MinBackoff << min(failures, 16)
	if d <= 0 || d > a.MaxBackoff {
		d = a.MaxBackoff
	}
	d = d/2 + time.Duration(rand.Int64N(int64(d/2)+1)) // jitter: probes must not reconnect in lockstep
	if failures <= 1 || failures%10 == 0 {
		a.Log.Printf("server unreachable: %v (retry in %s)", err, d.Round(time.Second))
	}
	return d
}

func (a *Agent) heartbeatLoop(ctx context.Context) {
	failures := 0
	for {
		reply, err := a.Client.Heartbeat(ctx, time.Since(a.started), int(a.running.Load()))
		next := time.Duration(a.heartbeatEvery.Load())
		switch {
		case ctx.Err() != nil:
			return
		case err != nil:
			failures++
			next = a.pause(err, failures)
		default:
			if failures > 0 {
				a.Log.Printf("connected again")
			}
			failures = 0
			a.heartbeats.Add(1)
			if s := reply.HeartbeatSeconds; s >= 5 && s <= 600 {
				a.heartbeatEvery.Store(int64(time.Duration(s) * time.Second))
				next = time.Duration(s) * time.Second
			}
			if reply.RotateCredentials {
				a.rotate(ctx)
			}
		}
		if !wait(ctx, next) {
			return
		}
	}
}

func (a *Agent) rotate(ctx context.Context) {
	creds, err := a.Client.Rotate(ctx)
	if err != nil {
		a.Log.Printf("credential rotation failed: %v", err)
		return
	}
	if a.ConfigPath != "" {
		if err := SaveCredentials(a.ConfigPath, creds); err != nil {
			// The old secret stays valid for a while; try again on the next heartbeat.
			a.Log.Printf("cannot save rotated credentials: %v", err)
			return
		}
	}
	a.Client.setCredentials(creds)
	a.Log.Printf("credentials rotated")
}

func (a *Agent) jobLoop(ctx context.Context) {
	failures := 0
	for ctx.Err() == nil {
		jobs, err := a.Client.Jobs(ctx, a.PollWait, a.Concurrency*4)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			failures++
			if !wait(ctx, a.pause(err, failures)) {
				return
			}
			continue
		}
		failures = 0
		if len(jobs) == 0 {
			continue // the long poll already waited
		}
		results := a.runJobs(ctx, jobs)
		for attempt := 1; ctx.Err() == nil; attempt++ {
			err := a.Client.Results(ctx, results)
			if err == nil || errors.Is(err, ErrRejected) || attempt >= 3 {
				if err != nil {
					a.Log.Printf("results not delivered: %v", err)
				}
				break
			}
			if !wait(ctx, a.pause(err, attempt)) {
				return
			}
		}
	}
}

// runJobs runs a batch with bounded concurrency: goroutines, not processes.
func (a *Agent) runJobs(ctx context.Context, jobs []Job) []Result {
	results := make([]Result, len(jobs))
	sem := make(chan struct{}, a.Concurrency)
	var wg sync.WaitGroup
	for i, job := range jobs {
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			a.running.Add(1)
			defer a.running.Add(-1)
			results[i] = a.Checker.Run(ctx, job)
		}()
	}
	wg.Wait()
	// A job whose ID itself was malformed cannot be answered; drop it rather than the batch.
	valid := results[:0]
	for _, r := range results {
		if jobIDRe.MatchString(r.JobID) {
			valid = append(valid, r)
		}
	}
	return valid
}
