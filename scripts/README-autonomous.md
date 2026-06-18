# Autonomous GSD Workflow

This script implements the autonomous workflow described in the OpenSpec document for executing milestone phases autonomously.

## Features

- Discovers phases from ROADMAP.md
- Executes each phase through discuss → plan → execute cycle
- Updates STATE.md after each phase
- Handles optional flags (--from, --to, --only, --interactive)

## Usage

```bash
# Run all incomplete phases
./scripts/autonomous_workflow.py

# Start from phase N
./scripts/autonomous_workflow.py --from 12

# Stop after phase N
./scripts/autonomous_workflow.py --to 13

# Execute only phase N
./scripts/autonomous_workflow.py --only 14

# Run in interactive mode
./scripts/autonomous_workflow.py --interactive
```

## How It Works

1. **Phase Discovery**: Parses `.planning/ROADMAP.md` to find all phases
2. **State Management**: Updates `.planning/STATE.md` after each phase
3. **Phase Execution**: For each phase:
   - **Discuss**: Creates context and validates requirements
   - **Plan**: Generates implementation plan
   - **Execute**: Runs the implementation
4. **Milestone Completion**: Runs audit and cleanup after all phases

## Files Created

- `.planning/phases/phase-{N}/CONTEXT.md` - Phase context
- `.planning/phases/phase-{N}/{N}-01-PLAN.md` - Implementation plan
- `.planning/phases/phase-{N}/{N}-SUMMARY.md` - Phase summary
- `.planning/milestone-AUDIT-{DATE}.md` - Milestone audit report