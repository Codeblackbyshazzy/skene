package main

import (
	"fmt"
	"time"

	"skene/internal/services/telemetry"
)

func main() {
	fmt.Println("Creating telemetry client (enabled=true)...")
	client := telemetry.NewClient(true)

	fmt.Println("Sending test event: telemetry_integration_test")
	client.Track("telemetry_integration_test", map[string]string{
		"source":    "manual_test",
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	})

	fmt.Println("Sending second event: tui_opened (simulated)")
	client.Track("tui_opened", map[string]string{
		"source": "manual_test",
	})

	fmt.Println("Closing client (flushing queue)...")
	client.Close()

	fmt.Println("Done! Check PostHog Live Events to see if they arrived.")
}
