// scat-probe: the Скат provider probe agent.
//
//	scat-probe enroll --api https://api.example --token skenr_... [--config PATH]
//	scat-probe run    [--config PATH]
//	scat-probe check  [--config PATH]
//	scat-probe version
//
// The probe only checks whether VPN endpoints are reachable from this network (TCP, TLS,
// HTTP GET). It does not run VLESS or Xray, does not act as a VPN, never executes
// commands and never opens a listening port.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"syscall"
	"time"

	"github.com/Pro100kir2/scat/probe/internal/agent"
)

// Set at build time: -ldflags "-X main.version=1.0.0 -X main.profile=linux-mipsle".
var (
	version = "0.0.0-dev"
	profile = ""
)

const defaultConfig = "/etc/scat-probe/probe.json"

func main() {
	// Routers have little RAM: keep the heap small and collect early.
	debug.SetGCPercent(50)
	debug.SetMemoryLimit(24 << 20)

	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "enroll":
		err = enroll(os.Args[2:])
	case "run":
		err = run(os.Args[2:])
	case "check":
		err = check(os.Args[2:])
	case "version", "--version", "-v":
		fmt.Printf("scat-probe %s %s %s/%s\n", version, profile, runtime.GOOS, runtime.GOARCH)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "scat-probe:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: scat-probe enroll|run|check|version [--config PATH]")
}

func platform() string { return runtime.GOOS + "/" + runtime.GOARCH }

func enroll(args []string) error {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	api := fs.String("api", "", "Скат API origin, e.g. https://api.scat-search.ru")
	token := fs.String("token", "", "one-time enrollment token from the website")
	config := fs.String("config", defaultConfig, "where to store the probe credentials")
	force := fs.Bool("force", false, "replace existing credentials")
	_ = fs.Parse(args)
	if *token == "" {
		return errors.New("--token is required")
	}
	base, err := agent.ValidateAPIURL(*api)
	if err != nil {
		return err
	}
	if _, err := os.Stat(*config); err == nil && !*force {
		return fmt.Errorf("%s already exists (use --force to re-enroll)", *config)
	}
	prof := profile
	if prof == "" {
		prof = "linux-" + runtime.GOARCH
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := agent.NewClient(agent.Credentials{APIURL: base}, version)
	creds, err := client.Register(ctx, *token, prof, platform())
	if err != nil {
		return fmt.Errorf("registration failed: %w", err)
	}
	if err := agent.SaveCredentials(*config, creds); err != nil {
		return err
	}
	fmt.Printf("registered probe %s\n", creds.ProbeID)
	return nil
}

func loadAgent(config string) (*agent.Agent, error) {
	creds, err := agent.LoadCredentials(config)
	if err != nil {
		return nil, fmt.Errorf("cannot load %s: %w (run `scat-probe enroll` first)", config, err)
	}
	return &agent.Agent{
		Client:     agent.NewClient(creds, version),
		Checker:    agent.NewChecker(version),
		ConfigPath: config,
	}, nil
}

func run(args []string) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	config := fs.String("config", defaultConfig, "probe credentials file")
	_ = fs.Parse(args)
	a, err := loadAgent(*config)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := a.Run(ctx); err != nil && !errors.Is(err, context.Canceled) {
		return err
	}
	return nil
}

// check sends one heartbeat: the installer uses it to confirm the probe is Online.
func check(args []string) error {
	fs := flag.NewFlagSet("check", flag.ExitOnError)
	config := fs.String("config", defaultConfig, "probe credentials file")
	_ = fs.Parse(args)
	a, err := loadAgent(*config)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	reply, err := a.Client.Heartbeat(ctx, 0, 0)
	if err != nil {
		return fmt.Errorf("heartbeat failed: %w", err)
	}
	fmt.Printf("probe %s online (status: %s)\n", a.Client.Credentials().ProbeID, reply.Status)
	return nil
}
