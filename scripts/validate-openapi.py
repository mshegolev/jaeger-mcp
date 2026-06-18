#!/usr/bin/env python3
"""
Script to validate OpenAPI specifications in the project.
"""

import yaml
import sys
from pathlib import Path


def validate_openapi_spec(file_path: str) -> bool:
    """Validate an OpenAPI specification file."""
    try:
        file_path = Path(file_path)
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            return False

        with open(file_path, "r") as f:
            spec = yaml.safe_load(f)

        # Basic validation
        if "openapi" not in spec:
            print(f"❌ Missing 'openapi' version in {file_path}")
            return False

        if "info" not in spec:
            print(f"❌ Missing 'info' section in {file_path}")
            return False

        if "title" not in spec["info"]:
            print(f"❌ Missing 'title' in info section of {file_path}")
            return False

        print(f"✅ {file_path} - {spec['info']['title']}")
        return True

    except yaml.YAMLError as e:
        print(f"❌ YAML error in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error validating {file_path}: {e}")
        return False


def main():
    """Main validation function."""
    specs_to_check = ["openapi.yaml", "docs/mcp-tools-openapi.yaml"]

    all_valid = True
    for spec_file in specs_to_check:
        if not validate_openapi_spec(spec_file):
            all_valid = False

    if all_valid:
        print("\n🎉 All OpenAPI specifications are valid!")
        return 0
    else:
        print("\n💥 Some OpenAPI specifications are invalid!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
