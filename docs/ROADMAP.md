# SMU Roadmap

## Vision

SMU is evolving into an AI-powered Content Operating System.

The goal is to help creators and businesses manage the complete content
lifecycle from one platform:

- generate ideas
- create content
- create media
- improve content
- schedule content
- publish everywhere
- analyse performance
- continuously improve future content

SMU should become the place where content strategy, creation, scheduling,
publishing and learning all connect.

## Completed Milestones

Major completed milestones:

- architecture refactor
- blueprint extraction
- service layer
- model extraction
- compatibility layer
- regression suite
- publishing service
- scheduler service
- caption service
- content service
- media service
- image generation service
- shared time utilities
- architecture documentation
- publishing contract documentation
- testing documentation
- environment documentation
- testing infrastructure

These milestones moved SMU away from a monolithic application shape while
preserving existing behavior, endpoint compatibility and publishing stability.

## Current Priorities

Current priorities:

- complete the documentation refresh
- refresh the changelog
- archive or label historical documentation
- add type hints where they clarify service contracts
- introduce static analysis
- continue structured logging improvements
- improve diagnostics for support and beta operations
- improve authentication boundaries where needed
- clean up configuration where safe

## Platform Publishing

Future publishing capabilities:

- LinkedIn carousel publishing
- Pinterest scheduling
- Threads support
- additional platforms
- platform-specific publishing rules
- improved Connected Accounts
- platform health monitoring
- publishing retries
- publishing analytics

Publishing work should continue to preserve payload compatibility and avoid
breaking existing Make.com workflows.

## AI Content Planning

Future planning capabilities:

- AI Content Planner
- campaign generation
- series planning
- monthly content calendars
- seasonal planning
- evergreen content suggestions
- content ideas
- CTA recommendations
- hashtag recommendations
- audience targeting
- brand voice optimisation

The planning experience should turn a goal or idea into a coherent content plan,
not just a single generated post.

## AI Studio

Future Studio improvements:

- multi-step rewriting
- tone libraries
- headline generation
- hook optimisation
- CTA optimisation
- SEO optimisation
- readability scoring
- brand consistency scoring
- A/B caption generation
- caption comparison

Studio should remain a creative assistant: it should improve the user's content
without hiding the user's judgment or voice.

## AI Memory & Knowledge

SMU's long-term AI direction is to evolve from content generation into content
memory.

Future capabilities:

- remember previously published content
- avoid duplicate ideas
- learn which posts perform best
- remember preferred brand voice
- remember successful CTAs
- remember successful hooks
- recognise seasonal content automatically
- suggest evergreen content at the right time
- identify content gaps
- build a searchable knowledge base of all published content
- recommend follow-up posts automatically
- learn from engagement over time

The goal is for SMU to become an AI marketing assistant that improves as it
learns more about each business.

## Media Roadmap

Future media capabilities:

- AI carousel generation
- image template library
- AI video generation
- thumbnail generation
- background removal
- automatic resizing
- brand asset management
- video captions
- short-form video workflows

Media features should support fast creation while respecting brand consistency
and platform-specific requirements.

## Analytics

Future analytics capabilities:

- engagement dashboard
- platform comparison
- growth tracking
- best posting times
- content scoring
- engagement trends
- campaign reporting
- AI recommendations
- performance forecasting

Analytics should feed back into planning and memory so each campaign improves the
next one.

## Workflow Automation

Future workflow capabilities:

- bulk publishing
- approval workflows
- content queues
- campaign scheduling
- automation rules
- draft pipelines
- content review
- publishing sequences

Automation should reduce repetitive work while keeping users in control of final
content and publishing decisions.

## API & Integrations

Future integrations:

- public API
- webhooks
- Make.com
- n8n
- Zapier
- Google Drive
- Dropbox
- OneDrive
- Google Calendar
- CRM integrations
- future third-party AI providers

Integration work should keep authentication, permissions and auditability clear.

## Team Features

Future collaboration capabilities:

- roles
- permissions
- shared workspaces
- approvals
- comments
- activity history
- review workflows
- team dashboards

Team features should make SMU useful for agencies, teams and businesses without
making the solo-user experience heavier.

## Engineering Roadmap

Future engineering priorities:

- typing
- static analysis
- performance optimisation
- structured logging
- observability
- background workers
- database migrations
- deployment improvements
- CI/CD
- security improvements
- API versioning
- scalability

Engineering work should continue the refactor pattern: small changes, clear
boundaries and regression coverage.

## Product Principles

- Behaviour-first development
- Stable public interfaces
- Backwards compatibility
- Strong regression testing
- Documentation-first mindset
- Incremental improvements
- AI should assist rather than replace creativity

## Near-Term Milestones

Practical next milestones:

- finish documentation cleanup
- archive obsolete documentation
- refresh `CHANGELOG.md`
- improve Connected Accounts
- add LinkedIn carousel publishing
- add Pinterest scheduling
- build an analytics dashboard MVP
- build an AI Content Planner MVP
- improve Brand Coach feedback

## Long-Term Vision

SMU should become an AI Content Operating System rather than only a publishing
tool.

Long-term workflow:

```text
One idea
-> AI researches the topic
-> AI creates written content
-> AI creates images and media
-> AI improves and optimises the content
-> AI schedules publishing
-> AI publishes to multiple platforms
-> AI measures engagement
-> AI remembers what worked
-> AI learns from every campaign
-> AI recommends the next best content automatically
```

The ultimate vision is for SMU to become an intelligent marketing assistant that
continuously improves with every piece of content a business creates.

## Related Documentation

- `ARCHITECTURE.md`
- `ENVIRONMENT.md`
- `PUBLISHING_CONTRACT.md`
- `TEST_PLAN.md`
