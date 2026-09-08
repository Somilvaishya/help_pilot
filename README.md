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
