#!/usr/bin/env python3
"""Generate 2000+ synthetic memories with realistic link patterns for graph eval.

Creates a fictional company "Voltis" (EV fleet management) with memories
accumulated over months across many sessions, teams, and domains.
Each memory is unique and realistic. Links connect related facts across domains.

Usage:
    MEMORIES_URL=http://localhost:8901 MEMORIES_API_KEY=god-is-an-astronaut \
    python eval/generate_synthetic_memories.py [--count 2000] [--seed-links] [--cleanup]
"""

import hashlib
import httpx
import json
import os
import random
import sys
import time
from pathlib import Path

from eval.setup_validation import DEFAULT_EVAL_MEMORIES_URL, resolve_eval_memories_url, validate_eval_setup

PREFIX = "eval/graph/synth"


def _client():
    url = resolve_eval_memories_url(DEFAULT_EVAL_MEMORIES_URL)
    key = os.environ.get("MEMORIES_API_KEY", "")
    setup_report = validate_eval_setup(
        memories_url=url,
        require_mcp=False,
        require_claude=False,
        allow_unsafe_target=os.environ.get("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    if not setup_report.ok:
        for message in setup_report.errors:
            print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(2)
    return httpx.Client(base_url=url, headers={"X-API-Key": key, "Content-Type": "application/json"}, timeout=30)


# === Memory templates organized by domain ===
# Each domain has parametrized templates that get filled with random values
# to produce thousands of unique, realistic memories

SERVICES = ["fleet-api", "vehicle-telemetry", "route-optimizer", "billing-engine",
            "driver-portal", "admin-dashboard", "notification-hub", "auth-service",
            "analytics-pipeline", "charging-manager", "maintenance-scheduler",
            "geofence-engine", "trip-recorder", "fuel-card-integrator", "compliance-reporter"]

PEOPLE = [
    ("Marcus Lee", "platform lead", "marcus.lee"),
    ("Priya Sharma", "infra engineer", "priya.sharma"),
    ("James Park", "API engineer", "james.park"),
    ("Lisa Chen", "DevOps engineer", "lisa.chen"),
    ("Chen Wei", "telemetry lead", "chen.wei"),
    ("Ana Rodriguez", "data engineer", "ana.rodriguez"),
    ("Omar Hassan", "edge engineer", "omar.hassan"),
    ("Sofia Martinez", "product lead", "sofia.martinez"),
    ("Raj Patel", "route engineer", "raj.patel"),
    ("Emma Thompson", "billing engineer", "emma.thompson"),
    ("Jake Wilson", "mobile lead", "jake.wilson"),
    ("Yuki Tanaka", "iOS engineer", "yuki.tanaka"),
    ("Alex Rivera", "Android engineer", "alex.rivera"),
    ("David Kim", "CTO", "david.kim"),
    ("Maria Santos", "VP Engineering", "maria.santos"),
    ("Tom Anderson", "Product Manager", "tom.anderson"),
    ("Lily Zhang", "Designer", "lily.zhang"),
    ("Ben Crawford", "QA Lead", "ben.crawford"),
    ("Nina Patel", "Security Engineer", "nina.patel"),
    ("Sam O'Brien", "SRE", "sam.obrien"),
]

TECHNOLOGIES = [
    "PostgreSQL", "TimescaleDB", "Neo4j", "Redis", "Kafka", "Elasticsearch",
    "BigQuery", "Flink", "Airflow", "dbt", "Terraform", "ArgoCD",
    "Prometheus", "Grafana", "Jaeger", "Loki", "PagerDuty",
    "Stripe", "Auth0", "TaxJar", "Twilio", "SendGrid", "Segment",
    "React Native", "Expo", "Zustand", "React Query", "Vitest",
    "Docker", "Kubernetes", "Istio", "Helm", "Kong",
    "GCP", "GKE", "Cloud SQL", "GCS", "BigQuery",
    "EMQX", "MQTT", "gRPC", "GraphQL", "REST",
]

PORTS = list(range(3000, 9999))
# Note: PORTS shuffle moved into generate_all() after random.seed(42) for reproducibility

CONFIG_PATHS = [
    "src/config/{}.ts", "config/{}.yaml", "deploy/values/{}.yaml",
    "charts/{}/values.yaml", "terraform/modules/{}/main.tf",
    "src/services/{}/config.ts", ".github/workflows/{}.yml",
    "docker/{}.Dockerfile", "scripts/{}.sh", "k8s/{}/deployment.yaml",
]

ERROR_CODES = ["ERR_{:03d}".format(i) for i in range(100, 999)]

METRICS = [
    "p99 latency", "error rate", "throughput", "CPU utilization",
    "memory usage", "disk I/O", "network bandwidth", "connection pool size",
    "cache hit rate", "queue depth", "consumer lag", "replication lag",
]

BUG_PATTERNS = [
    "memory leak in {} caused by unbounded {} growth",
    "race condition in {} when concurrent {} requests arrive",
    "timeout in {} due to {} lock contention",
    "data corruption in {} from {} encoding mismatch",
    "deadlock in {} between {} transactions and background jobs",
    "connection exhaustion in {} from {} not closing connections",
    "infinite loop in {} triggered by {} edge case input",
    "null pointer in {} when {} returns empty response",
    "off-by-one in {} pagination causing {} to be skipped",
    "timezone bug in {} converting {} timestamps incorrectly",
]


def _gen_config_memories(n=150):
    """Generate configuration/settings memories."""
    mems = []
    for i in range(n):
        svc = random.choice(SERVICES)
        path = random.choice(CONFIG_PATHS).format(svc.replace("-", "_"))
        port = PORTS[i % len(PORTS)]
        tech = random.choice(TECHNOLOGIES)
        timeout = random.choice([5, 10, 15, 30, 60, 120])
        retries = random.choice([1, 2, 3, 5])
        pool_size = random.choice([5, 10, 20, 50, 100])

        templates = [
            f"{svc} configuration at {path}. Listens on port {port}. Connection timeout {timeout}s. Max retries {retries}.",
            f"{svc} uses {tech} with pool size {pool_size}. Config in {path}. Health check interval 10s.",
            f"Environment variable {svc.upper().replace('-','_')}_PORT={port} controls the {svc} listener. Documented in {path}.",
            f"{svc} {tech} connection string stored in Vault at secret/{svc}/{tech.lower()}. Pool max {pool_size}, idle timeout {timeout}s.",
            f"Rate limit for {svc}: {pool_size * 10} requests/minute. Configured in {path}. Burst allowed up to {pool_size * 20}.",
            f"{svc} retry policy: {retries} attempts with exponential backoff starting at {timeout * 100}ms. Dead letter after exhaustion.",
            f"Feature flag {svc.replace('-','_')}_v2_enabled controls new {tech} integration. Managed in Unleash. Default: off in prod.",
            f"{svc} log level configurable via {svc.upper().replace('-','_')}_LOG_LEVEL env var. Default: info. Valid: debug, info, warn, error.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/config/{svc}"})
    return mems


def _gen_people_memories(n=100):
    """Generate team/people memories."""
    mems = []
    for i in range(n):
        person = PEOPLE[i % len(PEOPLE)]
        name, role, slack = person
        svc = random.choice(SERVICES)
        tech = random.choice(TECHNOLOGIES)
        day = random.choice(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])

        templates = [
            f"{name} ({role}) is the primary owner of {svc}. Slack: @{slack}. Timezone: US Pacific.",
            f"{name} specializes in {tech}. Joined the team in 20{random.randint(20,25)}. Previously at {random.choice(['Google', 'Amazon', 'Meta', 'Uber', 'Lyft', 'Tesla', 'Rivian'])}.",
            f"{name} leads the {day} architecture review. Focus areas: {tech} performance and {svc} reliability.",
            f"For {svc} escalations, contact {name} (@{slack}). Backup: {PEOPLE[(i+1) % len(PEOPLE)][0]}.",
            f"{name} wrote the {svc} design doc (ADR-{random.randint(1,50):03d}). Key decision: chose {tech} over {random.choice(TECHNOLOGIES)}.",
            f"{name}'s OKR Q{random.randint(1,4)}: improve {svc} {random.choice(METRICS)} by {random.randint(10,50)}%.",
            f"1:1 with {name}: discussed {svc} tech debt. Action items in Linear under {svc}-{random.randint(100,999)}.",
            f"{name} is on-call this week for {svc}. PagerDuty schedule: {role}-rotation.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/team"})
    return mems


def _gen_bug_memories(n=100):
    """Generate debugging/incident memories."""
    mems = []
    for i in range(n):
        svc = random.choice(SERVICES)
        tech = random.choice(TECHNOLOGIES)
        metric = random.choice(METRICS)
        person = random.choice(PEOPLE)
        severity = random.choice(["P0", "P1", "P2", "P3"])
        date = f"20{random.randint(25,26)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

        templates = [
            f"Incident {severity}-{random.randint(100,999)} ({date}): {svc} {metric} spiked to {random.randint(200,999)}% of baseline. Root cause: {tech} {random.choice(['timeout', 'OOM', 'crash', 'deadlock'])}. Fixed by {person[0]}.",
            f"Bug in {svc}: {random.choice(BUG_PATTERNS).format(svc, tech)}. Discovered during load test. Fix: {random.choice(['added timeout', 'increased pool size', 'added retry logic', 'fixed race condition', 'added circuit breaker'])}.",
            f"Postmortem {date}: {svc} outage lasted {random.randint(5,120)} minutes. Impact: {random.randint(100,10000)} requests failed. Action items: add {tech} monitoring, improve {svc} alerting.",
            f"Debugging session with {person[0]}: traced {svc} slowness to {tech} query taking {random.randint(1,30)}s. Solution: added index on {random.choice(['created_at', 'user_id', 'vehicle_id', 'status', 'org_id'])} column.",
            f"Rollback of {svc} deploy {date}. Error: {random.choice(ERROR_CODES)} in {tech} integration. Reverted to previous version. {person[0]} investigating.",
            f"Performance regression in {svc} after {tech} upgrade. {metric} degraded by {random.randint(10,80)}%. Mitigated by increasing {random.choice(['replicas', 'memory', 'CPU', 'connection pool'])}.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/incidents/{svc}"})
    return mems


def _gen_architecture_memories(n=150):
    """Generate architecture/design memories."""
    mems = []
    for i in range(n):
        svc = random.choice(SERVICES)
        tech1, tech2 = random.sample(TECHNOLOGIES, 2)
        person = random.choice(PEOPLE)
        port = PORTS[i % len(PORTS)]

        templates = [
            f"ADR-{random.randint(1,99):03d}: {svc} uses {tech1} for {random.choice(['data storage', 'message passing', 'caching', 'search', 'authentication', 'monitoring'])}. Considered {tech2} but rejected due to {random.choice(['cost', 'complexity', 'latency', 'team expertise', 'vendor lock-in'])}.",
            f"{svc} architecture: {random.choice(['event-driven', 'request-response', 'CQRS', 'saga pattern', 'choreography', 'orchestration'])} with {tech1}. Internal API on port {port}.",
            f"{svc} data model: {random.choice(['normalized relational', 'document-oriented', 'graph', 'time-series', 'key-value'])} in {tech1}. Schema version {random.randint(1,50)}.",
            f"{svc} communicates with {random.choice(SERVICES)} via {random.choice(['gRPC', 'REST', 'Kafka events', 'GraphQL', 'WebSocket'])}. Protocol buffers in proto/{svc.replace('-','_')}.proto.",
            f"{svc} deployment: {random.randint(2,10)} replicas. Resource limits: {random.randint(250,2000)}m CPU, {random.randint(256,4096)}Mi memory. HPA target {random.randint(50,80)}% CPU.",
            f"Service boundary: {svc} owns the {random.choice(['vehicle', 'user', 'billing', 'route', 'telemetry', 'notification', 'charging', 'maintenance'])} domain. No direct DB access from other services.",
            f"{svc} API contract: {random.choice(['OpenAPI 3.1', 'GraphQL schema', 'Protobuf', 'AsyncAPI'])} spec at docs/api/{svc}.yaml. Breaking changes require ADR.",
            f"{svc} observability: custom {random.choice(METRICS)} dashboard in Grafana. Alert: {random.choice(METRICS)} > {random.randint(50,500)}{random.choice(['ms', '%', 'req/s'])} triggers {random.choice(['P1', 'P2'])} page.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/arch/{svc}"})
    return mems


def _gen_vendor_memories(n=80):
    """Generate vendor/third-party memories."""
    vendors = [
        ("Stripe", "payments", "$24,000/yr + 2.9%"),
        ("Auth0", "identity", "$18,000/yr"),
        ("TaxJar", "tax calculation", "$4,800/yr"),
        ("Twilio", "SMS/voice", "$6,000/yr"),
        ("SendGrid", "email", "$3,600/yr"),
        ("Segment", "analytics", "$12,000/yr"),
        ("EMQX Cloud", "MQTT broker", "$36,000/yr"),
        ("Datadog", "monitoring", "$15,000/yr"),
        ("PagerDuty", "alerting", "$2,400/yr"),
        ("Cloudflare", "CDN/DNS", "$3,000/yr"),
        ("Sentry", "error tracking", "$5,400/yr"),
        ("LaunchDarkly", "feature flags", "$7,200/yr"),
        ("Confluent", "Kafka managed", "$42,000/yr"),
        ("MongoDB Atlas", "document DB", "$9,600/yr"),
        ("Elastic Cloud", "search", "$14,400/yr"),
    ]
    mems = []
    for i in range(n):
        vendor = vendors[i % len(vendors)]
        name, purpose, cost = vendor
        person = random.choice(PEOPLE)

        templates = [
            f"{name} contract: {purpose}. Annual cost {cost}. Renewal date 20{random.randint(26,28)}-{random.randint(1,12):02d}. Contact: {person[0]} ({person[2]}@voltis.io).",
            f"{name} API key stored in Vault at secret/vendors/{name.lower().replace(' ', '-')}. Rate limit: {random.randint(100,10000)} calls/minute. Support tier: {random.choice(['Basic', 'Professional', 'Enterprise'])}.",
            f"{name} integration: used by {random.choice(SERVICES)} for {purpose}. SDK version {random.randint(1,5)}.{random.randint(0,20)}.{random.randint(0,10)}. Last updated 20{random.randint(25,26)}-{random.randint(1,12):02d}.",
            f"{name} outage on 20{random.randint(25,26)}-{random.randint(1,12):02d}-{random.randint(1,28):02d} affected {purpose}. Duration: {random.randint(5,120)} minutes. Workaround: {random.choice(['failover to backup', 'cached responses', 'manual process', 'queue and retry'])}.",
            f"Evaluating {name} alternatives. {person[0]} running POC with {random.choice(['competitor A', 'open-source option', 'in-house solution'])}. Decision by EOQ.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/vendors"})
    return mems


def _gen_process_memories(n=100):
    """Generate process/workflow memories."""
    mems = []
    for i in range(n):
        svc = random.choice(SERVICES)
        person = random.choice(PEOPLE)

        templates = [
            f"Deploy process for {svc}: merge to main -> CI builds image -> ArgoCD syncs to staging -> smoke test -> manual promote to prod. Approver: {person[0]}.",
            f"Database migration for {svc}: create migration file -> review by {person[0]} -> apply to staging -> verify -> apply to prod during maintenance window ({random.choice(['Tuesday 2am', 'Wednesday 3am', 'Saturday 1am'])} UTC).",
            f"On-call handoff for {svc}: {random.choice(['Monday', 'Wednesday', 'Friday'])} at 10am Pacific. Outgoing writes summary in #oncall-{svc.split('-')[0]} Slack channel.",
            f"Code review for {svc}: 2 approvals required. {person[0]} is mandatory reviewer for {random.choice(['API changes', 'schema changes', 'infrastructure', 'security'])}.",
            f"Release process: tag v{random.randint(1,5)}.{random.randint(0,20)}.{random.randint(0,50)} -> changelog generated -> release notes by {person[0]} -> deploy to staging -> 24hr bake -> prod.",
            f"Incident response for {svc}: {person[0]} is incident commander. Communication via #incident-{random.randint(100,999)} Slack channel. Status page updated every 15 minutes.",
            f"Capacity planning for {svc}: reviewed quarterly by {person[0]}. Current utilization {random.randint(30,80)}%. Scaling event triggers at {random.randint(70,90)}%.",
            f"Security review for {svc}: annual penetration test by {random.choice(['NCC Group', 'Bishop Fox', 'Trail of Bits'])}. Last review: 20{random.randint(25,26)}-{random.randint(1,12):02d}. {random.randint(0,5)} findings.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/process/{svc}"})
    return mems


def _gen_meeting_memories(n=100):
    """Generate meeting notes / standup memories — the noise that fills real stores."""
    mems = []
    for i in range(n):
        person1, person2 = random.sample(PEOPLE, 2)
        svc = random.choice(SERVICES)
        date = f"20{random.randint(25,26)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

        templates = [
            f"Standup {date}: {person1[0]} working on {svc} {random.choice(['feature', 'bugfix', 'refactor', 'test coverage', 'documentation'])}. Blocker: waiting on {person2[0]} for {random.choice(['review', 'API spec', 'design', 'test data'])}.",
            f"Sprint retro {date}: {svc} team. Good: {random.choice(['deployment speed improved', 'fewer incidents', 'better test coverage'])}. Improve: {random.choice(['documentation', 'code review turnaround', 'on-call handoff'])}.",
            f"Architecture review {date}: discussed {svc} scaling. {person1[0]} proposed {random.choice(['horizontal scaling', 'caching layer', 'read replica', 'event sourcing', 'CQRS'])}. Decision: try in staging first.",
            f"1:1 {person1[0]} and {person2[0]} ({date}): discussed {svc} roadmap. Priority: {random.choice(['reliability', 'performance', 'new features', 'tech debt', 'security'])}. Next step: write RFC.",
            f"Planning poker {date}: {svc} stories estimated. Largest: {random.randint(5,21)} points for {random.choice(['API redesign', 'database migration', 'monitoring overhaul', 'auth integration'])}.",
        ]
        mems.append({"text": random.choice(templates), "source": f"{PREFIX}/meetings"})
    return mems


def generate_all(target_count=2000):
    """Generate all memory categories to reach target count."""
    global PORTS
    random.seed(42)  # Reproducible
    PORTS = list(range(3000, 9999))  # Reset to original state before shuffling
    random.shuffle(PORTS)  # Now deterministic across repeated calls

    all_mems = []
    # Proportional distribution matching real usage patterns
    all_mems.extend(_gen_config_memories(int(target_count * 0.15)))     # 15% config
    all_mems.extend(_gen_people_memories(int(target_count * 0.10)))     # 10% people
    all_mems.extend(_gen_bug_memories(int(target_count * 0.10)))        # 10% bugs
    all_mems.extend(_gen_architecture_memories(int(target_count * 0.20)))  # 20% arch
    all_mems.extend(_gen_vendor_memories(int(target_count * 0.08)))     # 8% vendors
    all_mems.extend(_gen_process_memories(int(target_count * 0.12)))    # 12% process
    all_mems.extend(_gen_meeting_memories(int(target_count * 0.25)))    # 25% meetings (noise)

    return all_mems[:target_count]


def seed_memories(client, memories, batch_size=200):
    """Seed memories in batches."""
    ids = []
    for start in range(0, len(memories), batch_size):
        batch = memories[start:start + batch_size]
        resp = client.post("/memory/add-batch", json={
            "memories": [{"text": m["text"], "source": m["source"]} for m in batch],
            "deduplicate": False,
        })
        resp.raise_for_status()
        batch_ids = resp.json().get("ids", [])
        ids.extend(batch_ids)
        print(f"  Seeded {start + len(batch)}/{len(memories)} ({len(batch_ids)} in batch)")
    return ids


def create_links(client, memory_ids, memories, link_count=200):
    """Create realistic links between related memories.

    Strategy: find pairs that share a service name or technology,
    then link them. This simulates what extraction maintenance would do.
    """
    # Build index: service -> memory indices
    service_index = {}
    tech_index = {}
    for i, mem in enumerate(memories):
        text = mem["text"].lower()
        for svc in SERVICES:
            if svc in text:
                service_index.setdefault(svc, []).append(i)
        for tech in TECHNOLOGIES:
            if tech.lower() in text:
                tech_index.setdefault(tech.lower(), []).append(i)

    links = set()

    # Cross-domain links: same service mentioned in different source categories
    for svc, indices in service_index.items():
        if len(indices) < 2:
            continue
        sources = {}
        for idx in indices:
            src = memories[idx]["source"]
            sources.setdefault(src, []).append(idx)
        # Link across different sources
        src_keys = list(sources.keys())
        for a in range(len(src_keys)):
            for b in range(a + 1, len(src_keys)):
                for idx_a in sources[src_keys[a]][:2]:
                    for idx_b in sources[src_keys[b]][:2]:
                        links.add((idx_a, idx_b))
                        if len(links) >= link_count:
                            break
                    if len(links) >= link_count:
                        break
                if len(links) >= link_count:
                    break
            if len(links) >= link_count:
                break
        if len(links) >= link_count:
            break

    # Tech-based links if we haven't hit target
    if len(links) < link_count:
        for tech, indices in tech_index.items():
            if len(indices) < 2:
                continue
            pairs = [(indices[i], indices[j]) for i in range(min(3, len(indices))) for j in range(i+1, min(4, len(indices)))]
            for a, b in pairs:
                if a != b:
                    links.add((a, b))
                if len(links) >= link_count:
                    break
            if len(links) >= link_count:
                break

    # Create links via API
    created = 0
    failed = 0
    for from_idx, to_idx in list(links)[:link_count]:
        from_id = memory_ids[from_idx]
        to_id = memory_ids[to_idx]
        resp = client.post(f"/memory/{from_id}/link", json={"to_id": to_id, "type": "related_to"})
        if resp.status_code == 200:
            created += 1
        else:
            failed += 1

    print(f"  Created {created} links ({failed} failed)")
    return created


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate synthetic memories for graph eval")
    parser.add_argument("--count", type=int, default=2000, help="Number of memories to generate")
    parser.add_argument("--links", type=int, default=200, help="Number of links to create")
    parser.add_argument("--cleanup", action="store_true", help="Only cleanup, don't seed")
    parser.add_argument("--seed-only", action="store_true", help="Seed without creating links")
    args = parser.parse_args()

    client = _client()

    # Health check
    try:
        r = client.get("/health/ready")
        r.raise_for_status()
        print("Connected: ready")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if args.cleanup:
        r = client.post("/memory/delete-by-prefix", json={"source_prefix": PREFIX})
        print(f"Cleaned up: {r.json()}")
        return

    # Clean first
    client.post("/memory/delete-by-prefix", json={"source_prefix": PREFIX})

    # Generate
    print(f"Generating {args.count} memories...")
    memories = generate_all(args.count)
    print(f"Generated {len(memories)} memories across {len(set(m['source'] for m in memories))} sources")

    # Seed
    print("Seeding...")
    start = time.time()
    memory_ids = seed_memories(client, memories)
    seed_time = time.time() - start
    print(f"Seeded {len(memory_ids)} memories in {seed_time:.1f}s")

    if args.seed_only:
        return

    # Create links
    print(f"Creating {args.links} links...")
    start = time.time()
    created = create_links(client, memory_ids, memories, args.links)
    link_time = time.time() - start
    print(f"Links created in {link_time:.1f}s")

    print(f"\nDone. {len(memory_ids)} memories + {created} links at {PREFIX}")


if __name__ == "__main__":
    main()
