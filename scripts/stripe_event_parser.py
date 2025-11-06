#!/usr/bin/env python3
"""
Parse Stripe CLI events list command output.

This script parses JSON output from the Stripe CLI 'events list' command
and extracts key information from customer.subscription.* events.

Usage:
    stripe events list --type="customer.subscription.trial_will_end" | python stripe_event_parser.py
    
Output format:
    event_id | event_type | stripe_customer_id | metadata_key1=value1,metadata_key2=value2

Example:
    evt_1N8... | customer.subscription.created | cus_ABC123 | trial_days=7,plan_type=premium
"""

import json
import sys
from typing import Dict, List, Optional


def flatten_metadata(metadata: Dict[str, str]) -> str:
    """
    Flatten a metadata dictionary into a comma-separated key=value string.
    
    Args:
        metadata: Dictionary of metadata key-value pairs
        
    Returns:
        Comma-separated string of key=value pairs, or empty string if no metadata
    """
    if not metadata:
        return ""
    
    return ",".join([f"{key}={value}" for key, value in metadata.items()])


def parse_stripe_event(event_data: Dict) -> Optional[str]:
    """
    Parse a single Stripe event and extract relevant information.
    
    Args:
        event_data: Dictionary containing Stripe event data
        
    Returns:
        Formatted string with event information, or None if not a customer.subscription event
    """
    # Check if this is a customer subscription event
    event_type = event_data.get("type", "")
    if not event_type.startswith("customer.subscription."):
        return None
    
    # Extract basic event information
    event_id = event_data.get("id", "")
    
    # Extract customer ID from the event data object
    event_object = event_data.get("data", {}).get("object", {})
    stripe_customer_id = event_object.get("customer", "")
    
    # Extract and flatten metadata
    metadata = event_object.get("metadata", {})
    flattened_metadata = flatten_metadata(metadata)
    
    # Format output
    return f"{event_id} | {event_type} | {stripe_customer_id} | {flattened_metadata}"


def main():
    """
    Main function to process Stripe CLI events list output.
    """
    try:
        # Read JSON data from stdin
        input_data = sys.stdin.read().strip()
        
        if not input_data:
            print("No input data provided", file=sys.stderr)
            sys.exit(1)
        
        # Parse JSON data
        try:
            events_data = json.loads(input_data)['data']
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}", file=sys.stderr)
            sys.exit(1)
        
        # Handle both single event and list of events
        if isinstance(events_data, dict):
            # Single event
            events = [events_data]
        elif isinstance(events_data, list):
            # List of events
            events = events_data
        else:
            # Check if it's the Stripe CLI format with 'data' field containing the list
            if "data" in events_data and isinstance(events_data["data"], list):
                events = events_data["data"]
            else:
                print("Unexpected JSON structure", file=sys.stderr)
                sys.exit(1)

        # Process each event
        parsed_count = 0
        for event in events:
            from pprint import pprint
            parsed_event = parse_stripe_event(event)
            if parsed_event:
                print(parsed_event)
                parsed_count += 1
        
        # Print summary to stderr
        print(f"Parsed {parsed_count} customer.subscription.* events", file=sys.stderr)
        
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
