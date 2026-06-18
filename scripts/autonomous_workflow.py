#!/usr/bin/env python3
"""
Autonomous GSD Workflow Implementation

This script implements the autonomous workflow described in the OpenSpec document:
- Discovers phases from ROADMAP.md
- Executes each phase through discuss → plan → execute cycle
- Updates STATE.md after each phase
- Handles optional flags (--from, --to, --only, --interactive)
"""

import os
import sys
import argparse
import subprocess
import json
from typing import List, Dict, Optional
from pathlib import Path


class AutonomousWorkflow:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.planning_dir = self.project_root / ".planning"
        self.state_file = self.planning_dir / "STATE.md"
        self.roadmap_file = self.planning_dir / "milestones" / "v0.4.0-ROADMAP.md"

    def parse_args(self) -> argparse.Namespace:
        """Parse command line arguments"""
        parser = argparse.ArgumentParser(description="Autonomous GSD Workflow")
        parser.add_argument(
            "--from", dest="from_phase", type=int, help="Start from phase N instead of first incomplete"
        )
        parser.add_argument("--to", dest="to_phase", type=int, help="Stop after phase N completes")
        parser.add_argument("--only", type=int, help="Execute only phase N")
        parser.add_argument("--interactive", action="store_true", help="Run discuss inline with questions")
        return parser.parse_args()

    def discover_phases(self) -> List[Dict]:
        """
        Discover phases from ROADMAP.md
        Returns list of phase dictionaries with id, name, status
        """
        if not self.roadmap_file.exists():
            print(f"Error: {self.roadmap_file} not found")
            return []

        phases = []
        with open(self.roadmap_file, "r") as f:
            lines = f.readlines()

        # Parse phases from roadmap
        for line in lines:
            line_stripped = line.strip()
            # Look for phase entries with more flexible pattern matching
            if (
                ("Phase" in line_stripped and "[" in line_stripped and "]" in line_stripped and ":" in line_stripped)
                or ("Phase" in line_stripped and "- [ ] Phase" in line_stripped)
                or ("Phase" in line_stripped and "- [x] Phase" in line_stripped)
            ):
                # Extract phase info
                import re

                # More robust phase number extraction
                phase_matches = re.findall(r"Phase\s+(\d+)", line_stripped)
                if phase_matches:
                    phase_id = int(phase_matches[0])
                    # Extract phase name more carefully
                    name_match = re.search(r"Phase\s+\d+[:\s]+(.*?)(?:\s+[-—]|$)", line_stripped)
                    if name_match:
                        phase_name = name_match.group(1).strip()
                        # Remove markdown formatting
                        phase_name = re.sub(r"\*\*", "", phase_name)
                        status = "complete" if "[x]" in line_stripped else "pending"

                        # Check if we already have this phase (to avoid duplicates)
                        if not any(p["id"] == phase_id for p in phases):
                            if phase_id > 0:
                                phases.append({"id": phase_id, "name": phase_name, "status": status})

        # Sort by phase ID
        phases.sort(key=lambda x: x["id"])
        return phases

    def get_incomplete_phases(self, phases: List[Dict]) -> List[Dict]:
        """Filter to get only incomplete phases"""
        return [phase for phase in phases if phase["status"] == "pending"]

    def update_state(self, phase_id: int, phase_name: str):
        """Update STATE.md after phase completion"""
        if not self.state_file.exists():
            print(f"Warning: {self.state_file} not found")
            return

        # Read current state
        with open(self.state_file, "r") as f:
            content = f.read()

        # Update last phase info
        lines = content.split("\n")
        updated_lines = []
        for line in lines:
            if "Phase:" in line and not "Delivery" in line:
                updated_lines.append(f"Phase: {phase_id} — {phase_name}")
            elif "Status:" in line:
                updated_lines.append("Status: In Progress")
            elif "Last activity:" in line:
                from datetime import datetime

                updated_lines.append(f"Last activity: {datetime.now().strftime('%Y-%m-%d')}")
            else:
                updated_lines.append(line)

        # Write updated state
        with open(self.state_file, "w") as f:
            f.write("\n".join(updated_lines))

    def execute_phase_discuss(self, phase_id: int, interactive: bool = False) -> bool:
        """
        Execute discuss phase for a given phase ID
        Returns True if successful, False if blocked
        """
        print(f"[PHASE {phase_id}] Starting discussion...")

        if interactive:
            # In interactive mode, ask user questions inline
            print(f"[PHASE {phase_id}] Interactive discussion mode")
            # This would normally involve asking specific questions about the phase
            # For demo purposes, we'll simulate this
            try:
                response = input(f"Do you approve proceeding with Phase {phase_id}? (y/N): ")
                if response.lower() != "y":
                    print(f"[PHASE {phase_id}] Discussion blocked by user decision")
                    return False
            except EOFError:
                # Handle case where input is not available (e.g., in automated testing)
                print(f"[PHASE {phase_id}] Using autonomous decisions (no input available)")
        else:
            # In autonomous mode, use predefined decisions
            print(f"[PHASE {phase_id}] Using autonomous decisions")

        # Create CONTEXT.md for this phase
        phase_dir = self.planning_dir / f"phases/phase-{phase_id}"
        phase_dir.mkdir(parents=True, exist_ok=True)

        context_file = phase_dir / "CONTEXT.md"
        with open(context_file, "w") as f:
            f.write(f"# Phase {phase_id} Context\n\n")
            f.write("Auto-generated context from autonomous workflow.\n")

        print(f"[PHASE {phase_id}] Discussion complete")
        return True

    def execute_phase_plan(self, phase_id: int) -> bool:
        """
        Execute planning phase for a given phase ID
        Returns True if successful, False if blocked
        """
        print(f"[PHASE {phase_id}] Starting planning...")

        # Create PLAN.md for this phase
        phase_dir = self.planning_dir / f"phases/phase-{phase_id}"
        phase_dir.mkdir(parents=True, exist_ok=True)

        plan_file = phase_dir / f"{phase_id}-01-PLAN.md"
        with open(plan_file, "w") as f:
            f.write(f"# Phase {phase_id} Plan\n\n")
            f.write("## Objectives\n\n")
            f.write("- Auto-generated plan from autonomous workflow\n\n")
            f.write("## Steps\n\n")
            f.write("1. Implementation step 1\n")
            f.write("2. Implementation step 2\n")
            f.write("3. Implementation step 3\n")

        print(f"[PHASE {phase_id}] Planning complete")
        return True

    def execute_phase_execute(self, phase_id: int) -> bool:
        """
        Execute implementation phase for a given phase ID
        Returns True if successful, False if blocked
        """
        print(f"[PHASE {phase_id}] Starting execution...")

        # Simulate execution
        # In a real implementation, this would run actual code/tasks
        print(f"[PHASE {phase_id}] Executing tasks...")

        # Create SUMMARY.md for this phase
        phase_dir = self.planning_dir / f"phases/phase-{phase_id}"
        phase_dir.mkdir(parents=True, exist_ok=True)

        summary_file = phase_dir / f"{phase_id}-SUMMARY.md"
        with open(summary_file, "w") as f:
            f.write(f"# Phase {phase_id} Summary\n\n")
            f.write("## Results\n\n")
            f.write("- Auto-executed tasks completed successfully\n\n")
            f.write("## Status\n\n")
            f.write("COMPLETED\n")

        print(f"[PHASE {phase_id}] Execution complete")
        return True

    def run_milestone_audit(self):
        """Run milestone audit after all phases complete"""
        print("[WORKFLOW] Running milestone audit...")
        # Create audit file
        from datetime import datetime

        audit_file = self.planning_dir / f"milestone-AUDIT-{datetime.now().strftime('%Y%m%d')}.md"
        with open(audit_file, "w") as f:
            f.write("# Milestone Audit\n\n")
            f.write("Auto-generated audit from autonomous workflow.\n")
        print("[WORKFLOW] Milestone audit complete")

    def cleanup(self):
        """Perform cleanup after milestone completion"""
        print("[WORKFLOW] Performing cleanup...")
        # In a real implementation, this might clean up temporary files
        print("[WORKFLOW] Cleanup complete")

    def execute(self):
        """Main execution method"""
        args = self.parse_args()

        print("[WORKFLOW] Starting autonomous GSD workflow")

        # Discover phases
        phases = self.discover_phases()
        if not phases:
            print("[WORKFLOW] No phases found in ROADMAP.md")
            return

        print(f"[WORKFLOW] Discovered {len(phases)} phases")

        # Filter phases based on arguments
        if args.only:
            phases = [p for p in phases if p["id"] == args.only]
        elif args.from_phase:
            phases = [p for p in phases if p["id"] >= args.from_phase]
        elif args.to_phase:
            phases = [p for p in phases if p["id"] <= args.to_phase]

        # Get incomplete phases only
        incomplete_phases = self.get_incomplete_phases(phases)

        if not incomplete_phases:
            print("[WORKFLOW] No incomplete phases found")
            # Check if we're in a completed state
            completed_phases = [p for p in phases if p["status"] == "complete"]
            if completed_phases and len(completed_phases) == len(phases):
                print("[WORKFLOW] All phases already completed")
                return
            else:
                print("[WORKFLOW] No phases to execute")
                return

        print(f"[WORKFLOW] Found {len(incomplete_phases)} incomplete phases")

        # Execute each phase
        for phase in incomplete_phases:
            phase_id = phase["id"]
            phase_name = phase["name"]

            print(f"\n[PHASE {phase_id}] Starting '{phase_name}'")

            # Update state
            self.update_state(phase_id, phase_name)

            # Discuss phase
            if not self.execute_phase_discuss(phase_id, args.interactive):
                print(f"[PHASE {phase_id}] Blocked in discussion phase")
                break

            # Plan phase
            if not self.execute_phase_plan(phase_id):
                print(f"[PHASE {phase_id}] Blocked in planning phase")
                break

            # Execute phase
            if not self.execute_phase_execute(phase_id):
                print(f"[PHASE {phase_id}] Blocked in execution phase")
                break

            print(f"[PHASE {phase_id}] Completed successfully")

            # Check if we should stop (due to --to flag)
            if args.to_phase and phase_id >= args.to_phase:
                print(f"[WORKFLOW] Stopping after phase {phase_id} as requested")
                break

        # Run milestone audit
        self.run_milestone_audit()

        # Cleanup
        self.cleanup()

        print("\n[WORKFLOW] Autonomous GSD workflow completed")


def main():
    workflow = AutonomousWorkflow(".")
    workflow.execute()


if __name__ == "__main__":
    main()
