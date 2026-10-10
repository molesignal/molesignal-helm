# Security policy

Report a vulnerability in this chart, or in what it deploys, as described in the application's
[security policy](https://github.com/molesignal/molesignal/blob/main/SECURITY.md). Please do not
open a public issue for it.

Things in this repository that are security-relevant and worth a careful look: the generation and
retention of the secrets (`templates/secret.yaml`, `templates/_helpers.tpl`), the security contexts
of the bundled PostgreSQL and RustFS, and the guidance on `/metrics`, which the application serves
without authentication.
