# SHLUZ Project Context

## Goal
SaaS platform providing access to AI models through a controlled API gateway.

## Current architecture

Clients:
- Telegram bot
- Desktop client (planned)
- Web demo (planned)

All clients communicate through Shluz API Gateway.

## Components

### API Gateway
Existing FastAPI service responsible for:
- authentication;
- providers routing;
- tariffs and limits;
- usage accounting.

### Telegram Bot
Current component:
- telegram_bot/
- gateway client scaffold.

Future:
- user registration;
- chat history;
- subscriptions;
- polished Telegram UX.

## Open decisions

- AI providers are not finalized.
- Telegram and Desktop may use different pricing models.
- Payment integration is future work.
