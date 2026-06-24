"""Create a client's dashboard login — run this after a client signs up.

You pick (or auto-generate) a password, this stores the hash, and prints the
credentials once for you to hand over. The client logs in at /login and sees
only their own tenant's leads.

Run:
  python create_account.py --tenant lockedout-locksmiths --username mick
  python create_account.py --tenant lockedout-locksmiths --username mick --password hunter2
"""

import argparse
import secrets
import sys

import accounts
import tenants


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a client dashboard login.")
    ap.add_argument("--tenant", required=True, help="tenant id (tenants/<id>.json)")
    ap.add_argument("--username", required=True, help="login username for the client")
    ap.add_argument("--password", help="password (omit to auto-generate a strong one)")
    args = ap.parse_args()

    tenants.load_tenant(args.tenant)  # tenant must exist + validate

    password = args.password or secrets.token_urlsafe(10)
    accounts.set_account(args.tenant, args.username, password)

    print("\nAccount created. Hand these to the client (they can change nothing yet):\n")
    print(f"  Login page: <your-app-url>/login")
    print(f"  Username:   {args.username}")
    print(f"  Password:   {password}")
    print(f"  Sees:       {args.tenant} dashboard only\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
