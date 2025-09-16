"""Small helper to test SMTP sending of the verification email.

Usage:
    SMTP_HOST=... SMTP_PORT=... SMTP_USER=... SMTP_PASS=... python3 app/send_test.py recipient@example.com

This will invoke generate_and_send_verification to create a token and attempt to send.
"""
import os
import sys
import asyncio
from app import utils

async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 app/send_test.py recipient@example.com")
        return
    recipient = sys.argv[1]
    token = await utils.generate_and_send_verification(recipient)
    print("Token generated:", token)

if __name__ == '__main__':
    asyncio.run(main())
