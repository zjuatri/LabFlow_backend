This folder contains the split route modules.

- `router.py`: combines sub-routers and exported as `app.api.routes.router`.
- `system.py`: health/root.
- `auth.py`: auth register/login.
- `projects.py`: project CRUD.
- `ai.py`: DeepSeek chat.
- `cleanup.py`: background cleanup task.

Next step: migrate the remaining endpoints from the old `app/api/routes.py` into
new modules (Typst render, charts, uploads, image crop, etc.).

Until that migration is complete, keep using the existing `app/api/routes.py`.
