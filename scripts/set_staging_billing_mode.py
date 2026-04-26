from __future__ import annotations

import argparse
from pathlib import Path


def _replace_keys(contents: str, replacements: dict[str, str]) -> str:
    lines = contents.splitlines()
    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        replaced = False
        for key, value in replacements.items():
            if line.startswith(f"{key}="):
                updated_lines.append(f"{key}={value}")
                seen.add(key)
                replaced = True
                break
        if not replaced:
            updated_lines.append(line)

    for key, value in replacements.items():
        if key not in seen:
            updated_lines.append(f"{key}={value}")

    updated = "\n".join(updated_lines)
    if contents.endswith("\n"):
        updated += "\n"
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set the intended billing posture for staging.",
    )
    parser.add_argument("--mode", choices=("disabled", "test_stripe"), required=True, help="Intended staging billing mode.")
    parser.add_argument("--publishable-key", default="", help="Stripe publishable key for test_stripe mode.")
    parser.add_argument("--secret-key", default="", help="Stripe secret key for test_stripe mode.")
    parser.add_argument("--webhook-secret", default="", help="Stripe webhook secret for test_stripe mode.")
    parser.add_argument("env_file", nargs="?", default=".env.staging", help="Path to the staging env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}")
        return 1

    replacements = {
        "STAGING_BILLING_MODE": args.mode,
    }

    if args.mode == "disabled":
        replacements.update(
            {
                "STRIPE_PUBLISHABLE_KEY": "",
                "STRIPE_SECRET_KEY": "",
                "STRIPE_WEBHOOK_SECRET": "",
            }
        )
    else:
        replacements.update(
            {
                "STRIPE_PUBLISHABLE_KEY": args.publishable_key.strip(),
                "STRIPE_SECRET_KEY": args.secret_key.strip(),
                "STRIPE_WEBHOOK_SECRET": args.webhook_secret.strip(),
            }
        )

    original = env_path.read_text(encoding="utf-8")
    updated = _replace_keys(original, replacements)
    env_path.write_text(updated, encoding="utf-8")

    print(f"Updated billing posture in {env_path}")
    print(f"STAGING_BILLING_MODE={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
