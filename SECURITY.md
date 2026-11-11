# Security Policy

## Reporting a vulnerability

Please report security issues privately by email to **info@neutron.com.bd** rather than opening a public issue. Include steps to reproduce and the affected version or commit. We aim to respond within 7 days.

## Deployment notes

- Always set `FLASK_SECRET_KEY`, `AIENERGY_PASSWORD` and `MYSQL_PASSWORD` through environment variables or a `.env` file. Never commit them.
- Use a read-only database account for the dashboard where possible.
- Model files in `LinearRegression/` are Python pickles. Loading a pickle can execute code, so only load models you trained yourself or fully trust.
- The dashboard is intended for internal networks. Put it behind HTTPS and a reverse proxy if it must be exposed.
