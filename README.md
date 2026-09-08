# Help Pilot

Organization-wide help ticket management system for Frappe.

Any employee can raise a ticket against any department (IT, ERP, Sales, HR, Accounts,
...). Tickets are routed to the responsible department, and row-level isolation makes
sure a requester only ever sees their own tickets.

## Doctypes

| Doctype | Purpose |
| --- | --- |
| HP Department | Department master + members (agents / department admins) |
| HP Department Member | Child table mapping a user to a department with a member role |
| HP Ticket | The ticket itself |
| HP Ticket Comment | Threaded conversation + internal notes |

## Roles

| Role | Scope |
| --- | --- |
| HP User | Raise tickets, see and comment on only their own |
| HP Agent | Act on tickets of the departments they are a member of |
| HP Department Admin | Full rights + reassignment within their departments |
| HP System Admin | Everything, across all departments |

## Install

```bash
bench get-app help_pilot https://github.com/Somilvaishya/help_pilot.git
bench --site <site> install-app help_pilot
```

## License

MIT

## Multi-site: one hub, many ERP sites

Frappe gives every site its own database, so there is no shared ticket table.
One site is the **hub** and holds every `HP Ticket`; the others run
[`help_pilot_client`](https://github.com/Somilvaishya/help_pilot_client) and
relay tickets to it over the REST API.

### Setting up a client site

On the **hub**:

1. Create a user for that site, e.g. `bridge-regency@company.com`, whose **only**
   role is `HP Bridge`. Generate its API key and secret.
2. Add an `HP Source Site` row: the site name exactly as it appears in the bench,
   its base URL, that bridge user, and a default department.

`HP Source Site` refuses to save if the bridge account also holds Agent,
Department Admin, System Admin or System Manager. That is deliberate — the
credential ends up in another site's `site_config.json`, so it must never be
able to read the ticket table.

On the **client site**, install `help_pilot_client` and add the hub details to
`site_config.json`. See that app's README.

### What a bridge account can do

| | |
| --- | --- |
| Raise a ticket for a named email | yes |
| Read tickets from **its own site**, for the email it names | yes |
| Read another site's tickets | no |
| Read a different person's tickets | no |
| See internal notes | no |
| Read the ticket table directly | no — the role grants no permission |
| Set a status other than Closed / Reopened | no |

### Requesters from other sites

They are auto-created on first ticket as **Website Users with no roles**: the hub
is a destination for their tickets, not somewhere they sign in.

Other apps on the hub hook user creation and hand out their own role — `suite`
adds `Suite User` — and any role with desk access flips the account to System
User. Help Pilot undoes that, so filing a ticket never turns an employee into a
desk user on the hub. Set `help_pilot_allow_desk_requesters: true` in
`site_config.json` to keep whatever the other apps decided.
