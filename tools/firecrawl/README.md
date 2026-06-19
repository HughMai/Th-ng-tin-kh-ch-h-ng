# Firecrawl — prospect scraping

Scrapes Wollongong trades (electricians first) into a qualified prospect list
for speed-to-lead outreach. See the 3-step pipeline: `/search` → `/scrape` (JSON
schema) → `/map`.

## Setup

- Key lives in `.env` (gitignored — never commit it). `.env.example` is the template.
- Load it: `FIRECRAWL_API_KEY` from this folder.

## Qualifying schema

Fields that map to the speed-to-lead pitch: `business_name`, `owner_name`,
`phone`, `email`, `suburb`, `services`, `has_contact_form`, `has_live_chat`,
`mentions_24_7`, `response_promise`. The wound = contact form + no instant
response.
