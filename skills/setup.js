#!/usr/bin/env node

// setup.js — One-script installer for @skene/database-skills.
//
// Usage (AI agent or terminal):
//   npx @skene/database-skills crm                          # preset
//   npx @skene/database-skills crm --db $DATABASE_URL       # with connection
//   npx @skene/database-skills crm,pipeline,support --seed  # custom skills
//   npx @skene/database-skills                              # interactive
//
// Presets: crm, helpdesk, billing, project, marketing, full

import pg from 'pg';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { execSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Presets ─────────────────────────────────────────────────────

const PRESETS = {
  crm:       ['identity', 'crm', 'pipeline', 'comms', 'analytics'],
  helpdesk:  ['identity', 'crm', 'support', 'comms', 'content', 'knowledge', 'analytics'],
  billing:   ['identity', 'crm', 'billing', 'commerce', 'analytics'],
  project:   ['identity', 'tasks', 'content', 'calendar', 'analytics'],
  marketing: ['identity', 'crm', 'campaigns', 'forms', 'analytics'],
  full:      null, // resolved to all skills
};

// ── Lifecycle map (for Skene Cloud promotion) ───────────────────

const LIFECYCLES = [
  { skill: 'crm',      entity: 'Contact',      stages: 'lead → prospect → customer → partner' },
  { skill: 'pipeline',  entity: 'Deal',         stages: 'discovery → qualified → proposal → closed won' },
  { skill: 'support',   entity: 'Ticket',       stages: 'open → pending → resolved → closed' },
  { skill: 'billing',   entity: 'Subscription', stages: 'trialing → active → past_due → canceled' },
  { skill: 'billing',   entity: 'Invoice',      stages: 'draft → open → paid → void' },
  { skill: 'tasks',     entity: 'Task',         stages: 'todo → in_progress → in_review → done' },
  { skill: 'content',   entity: 'Document',     stages: 'draft → published → archived' },
];

// ── Manifest loading & dependency resolution ────────────────────

function loadManifests() {
  const manifests = new Map();
  for (const entry of readdirSync(__dirname, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const p = join(__dirname, entry.name, 'manifest.json');
    if (!existsSync(p)) continue;
    manifests.set(entry.name, JSON.parse(readFileSync(p, 'utf-8')));
  }
  return manifests;
}

function resolveSkills(selected, manifests) {
  const resolved = new Set();
  function walk(name) {
    if (resolved.has(name)) return;
    const m = manifests.get(name);
    if (!m) throw new Error(`Unknown skill: ${name}`);
    for (const dep of m.depends_on || []) walk(dep);
    resolved.add(name);
  }
  for (const s of selected) walk(s);
  return [...resolved];
}

function topoSort(skills, manifests) {
  const set = new Set(skills);
  const inDeg = new Map(), adj = new Map();
  for (const n of set) { inDeg.set(n, 0); adj.set(n, []); }
  for (const n of set) {
    for (const d of (manifests.get(n).depends_on || [])) {
      if (set.has(d)) { adj.get(d).push(n); inDeg.set(n, inDeg.get(n) + 1); }
    }
  }
  const q = [...inDeg.entries()].filter(([, d]) => d === 0).map(([n]) => n);
  const sorted = [];
  while (q.length) {
    const c = q.shift();
    sorted.push(c);
    for (const nb of adj.get(c)) {
      inDeg.set(nb, inDeg.get(nb) - 1);
      if (inDeg.get(nb) === 0) q.push(nb);
    }
  }
  if (sorted.length !== set.size) throw new Error('Circular dependency in skills');
  return sorted;
}

// ── Interactive prompt (fallback when no args) ──────────────────

function ask(question) {
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  return new Promise((resolve) => {
    rl.question(question, (answer) => { rl.close(); resolve(answer.trim()); });
  });
}

// ── Supabase connection detection ───────────────────────────────

function detectSupabase() {
  // 1. Environment variables (silent — don't echo each one)
  for (const key of ['DATABASE_URL', 'SUPABASE_DB_URL', 'POSTGRES_URL']) {
    if (process.env[key]) {
      return { url: process.env[key], source: key };
    }
  }

  // 2. Supabase CLI — local dev instance
  try {
    const status = execSync('supabase status --output json 2>/dev/null', {
      encoding: 'utf-8',
      timeout: 5000,
    });
    const parsed = JSON.parse(status);
    if (parsed.DB_URL) {
      return { url: parsed.DB_URL, source: 'supabase cli (local)' };
    }
  } catch {}

  // 3. Supabase CLI — linked remote project
  try {
    const dbUrl = execSync('supabase db url 2>/dev/null', {
      encoding: 'utf-8',
      timeout: 5000,
    }).trim();
    if (dbUrl.startsWith('postgresql://') || dbUrl.startsWith('postgres://')) {
      return { url: dbUrl, source: 'supabase cli (linked project)' };
    }
  } catch {}

  return null;
}

// ── Connection error classification ────────────────────────────

function classifyConnectionError(err, dbUrl) {
  const code = err.code || '';
  const msg = err.message || '';

  let host = '';
  try { host = new URL(dbUrl).hostname; } catch {}
  const isSupabaseDirect = host.includes('.supabase.co') && !host.includes('pooler');
  const isSupabasePooler = host.includes('pooler.supabase.com') || host.includes('pooler.supabase.co');

  if (code === 'ENOTFOUND') {
    return {
      category: 'DNS',
      message: `Cannot resolve hostname: ${host}`,
      suggestions: [
        'Check the hostname in your connection string',
        'Verify your project exists in the Supabase dashboard',
        isSupabaseDirect ? 'Try the connection pooler URL instead (Dashboard → Settings → Database → Connection Pooling)' : null,
      ].filter(Boolean),
    };
  }

  if (code === 'ECONNREFUSED') {
    return {
      category: 'Connection refused',
      message: `Port closed or not listening on ${host}`,
      suggestions: [
        'If using Supabase local dev: run supabase start first',
        'Check the port number in your connection string',
        isSupabaseDirect ? 'The direct connection host may be IPv6-only — try the pooler URL from Dashboard → Settings → Database → Connection Pooling' : null,
      ].filter(Boolean),
    };
  }

  if (code === 'ETIMEDOUT' || code === 'EHOSTUNREACH' || code === 'ENETUNREACH') {
    return {
      category: 'Network unreachable',
      message: `Cannot reach ${host} (${code})`,
      suggestions: [
        isSupabaseDirect
          ? 'Supabase direct connections use IPv6 only (AAAA records). Your network may not support IPv6.'
          : 'Check your network connection and firewall settings',
        'Try the Supabase connection pooler instead (Dashboard → Settings → Database → Connection Pooling)',
        'Or apply migrations via Supabase MCP tools (see instructions below)',
      ],
    };
  }

  if (code === '28P01' || code === '28000' || msg.includes('password authentication failed')) {
    return {
      category: 'Authentication failed',
      message: 'Password rejected by the database server',
      suggestions: [
        'Reset your database password in the Supabase dashboard (Settings → Database)',
        'Make sure you are using the database password, not the Supabase API key',
        isSupabasePooler ? 'The connection pooler may require the pooler-specific password format' : null,
        'Copy a fresh connection string from Dashboard → Settings → Database → Connection string',
      ].filter(Boolean),
    };
  }

  if (msg.includes('SSL') || msg.includes('ssl') || code === 'DEPTH_ZERO_SELF_SIGNED_CERT') {
    return {
      category: 'SSL error',
      message: msg,
      suggestions: [
        'The connection is configured with SSL — make sure the server supports it',
        'If using a local database, try adding ?sslmode=disable to the connection string',
      ],
    };
  }

  if (code === 'ECONNRESET') {
    return {
      category: 'Connection reset',
      message: 'The server closed the connection unexpectedly',
      suggestions: [
        'This may indicate a firewall or proxy intercepting the connection',
        'Try a different connection method (pooler vs direct)',
        'If the problem persists, check the Supabase dashboard for project status',
      ],
    };
  }

  return {
    category: 'Connection error',
    message: msg,
    suggestions: [
      'Check your DATABASE_URL and try again',
      'Make sure your Supabase project is active (not paused)',
      'Try the connection pooler URL from Dashboard → Settings → Database → Connection Pooling',
    ],
  };
}

// ── Self-install into project ───────────────────────────────────

// ensureInstalled() removed — npx handles downloading the package.
// Users who want a permanent dep can run: npm install @skene/database-skills

// ── Main ────────────────────────────────────────────────────────

async function main() {
  const manifests = loadManifests();
  const allSkills = [...manifests.keys()];

  // Parse CLI args
  const args = process.argv.slice(2);
  let target = null, dbUrl = null, seed = false, dryRun = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--db' && args[i + 1]) { dbUrl = args[++i]; }
    else if (args[i] === '--seed') { seed = true; }
    else if (args[i] === '--dry-run' || args[i] === '--plan') { dryRun = true; }
    else if (args[i] === '--help' || args[i] === '-h') { printUsage(); process.exit(0); }
    else if (!target) { target = args[i]; }
  }

  // Interactive fallback
  if (!target) {
    console.log('\nSkene Database Skills');
    console.log('Backend schemas for your Supabase project.\n');
    console.log('Presets:');
    console.log('  crm        — contacts, companies, deals, comms, analytics');
    console.log('  helpdesk   — tickets, knowledge base, comms, analytics');
    console.log('  billing    — subscriptions, invoices, commerce, analytics');
    console.log('  project    — tasks, documents, calendar, analytics');
    console.log('  marketing  — campaigns, forms, analytics');
    console.log('  full       — all 19 skills');
    console.log('  (or comma-separated skill names)\n');

    target = await ask('What are you building? > ');
    if (!target) { console.log('Cancelled.'); process.exit(0); }
  }

  // Resolve skills (before connection — so we can always show the plan)
  let selected;
  if (target in PRESETS) {
    selected = PRESETS[target] || allSkills;
  } else {
    // Comma-separated skill names
    const names = target.split(',').map((s) => s.trim()).filter(Boolean);
    if (!names.includes('identity')) names.unshift('identity');
    selected = names;
  }

  const installOrder = topoSort(resolveSkills(selected, manifests), manifests);

  console.log(`\nInstall order: ${installOrder.join(' → ')}`);

  // Always show what they're getting — this is the key conversion moment
  printSkillSummary(installOrder, manifests);
  printPromotion(installOrder);

  // Dry-run: show manual instructions and exit
  if (dryRun) {
    printManualInstructions(installOrder);
    process.exit(0);
  }

  // ── Connection phase ───────────────────────────────────────────

  // Detect Supabase connection: --db flag → env vars → Supabase CLI → ask
  if (!dbUrl) {
    const detected = detectSupabase();
    if (detected) {
      dbUrl = detected.url;
      console.log(`  ✓ Found database via ${detected.source}`);
    }
  }

  if (!dbUrl) {
    console.log('\n  No database connection found.\n');
    console.log('  If your AI agent has Supabase MCP tools, use those instead —');
    console.log('  they work without a connection string (see instructions below).\n');
    console.log('  Otherwise, provide a connection string:');
    console.log('  • Set DATABASE_URL in your environment');
    console.log('  • Run supabase link to connect the Supabase CLI');
    console.log('  • Paste your connection string below\n');
    console.log('  Find it in: Supabase Dashboard → Settings → Database → Connection string\n');
    dbUrl = await ask('Database URL (or press Enter for manual instructions) > ');
    if (!dbUrl) {
      console.log('');
      printManualInstructions(installOrder);
      process.exit(0);
    }
  }

  // Connect
  const client = new pg.Client({
    connectionString: dbUrl,
    ssl: { rejectUnauthorized: false },
    statement_timeout: 30_000,
  });

  try {
    await client.connect();
  } catch (err) {
    const diag = classifyConnectionError(err, dbUrl);
    console.error(`\n  Connection failed: ${diag.category}`);
    console.error(`  ${diag.message}\n`);
    for (const s of diag.suggestions) {
      console.error(`  · ${s}`);
    }
    console.log('');
    printManualInstructions(installOrder);
    process.exit(1);
  }

  console.log(`Connected to ${new URL(dbUrl).hostname}`);

  // Pre-flight: detect existing tables
  const { rows } = await client.query(
    `SELECT tablename FROM pg_tables WHERE schemaname = 'public'`
  );
  const existing = new Set(rows.map((r) => r.tablename));

  const toInstall = [];
  const skipped = [];

  for (const name of installOrder) {
    const tables = manifests.get(name).tables || [];
    const allExist = tables.length > 0 && tables.every((t) => existing.has(t));
    const someExist = !allExist && tables.some((t) => existing.has(t));

    if (allExist) {
      skipped.push(name);
    } else if (someExist) {
      console.log(`⚠ ${name}: partially installed (some tables exist). Including in migration.`);
      toInstall.push(name);
    } else {
      toInstall.push(name);
    }
  }

  if (skipped.length > 0) {
    console.log(`Already installed: ${skipped.join(', ')} (skipping)`);
  }

  if (toInstall.length === 0) {
    console.log('\nAll selected skills are already installed!');
    await client.end();
    return;
  }

  // Apply migrations
  console.log(`\nApplying migrations...`);
  let tableCount = 0;

  for (const name of toInstall) {
    const sqlPath = join(__dirname, name, 'migration.sql');
    if (!existsSync(sqlPath)) {
      console.log(`  ⚠ ${name}/migration.sql not found, skipping`);
      continue;
    }
    const sql = readFileSync(sqlPath, 'utf-8');
    try {
      await client.query('BEGIN');
      await client.query(sql);
      await client.query('COMMIT');
      const tables = (manifests.get(name).tables || []).length;
      tableCount += tables;
      console.log(`  ✓ ${name} (${tables} tables)`);
    } catch (err) {
      await client.query('ROLLBACK');
      console.error(`  ✗ ${name}: ${err.message}`);
      await client.end();
      process.exit(1);
    }
  }

  console.log(`\nDone! ${toInstall.length} skills, ${tableCount} tables with RLS.`);

  // Seed data
  if (seed) {
    console.log('\nSeeding demo data...');
    for (const name of toInstall) {
      const sqlPath = join(__dirname, name, 'seed.sql');
      if (!existsSync(sqlPath)) continue;
      const sql = readFileSync(sqlPath, 'utf-8');
      try {
        await client.query(sql);
        console.log(`  ✓ ${name}`);
      } catch (err) {
        console.log(`  ⚠ ${name}: ${err.message}`);
      }
    }
  }

  await client.end();
}

// ── Skill summary ──────────────────────────────────────────────

function printSkillSummary(installOrder, manifests) {
  const maxName = Math.max(...installOrder.map((n) => n.length));

  console.log('\n  Skills to install:\n');
  for (const name of installOrder) {
    const tables = manifests.get(name).tables || [];
    const count = `${tables.length} table${tables.length === 1 ? '' : 's'}`;
    console.log(`    ${name.padEnd(maxName + 2)} ${count.padEnd(10)} ${tables.join(', ')}`);
  }
  console.log('');
}

// ── Manual installation instructions ───────────────────────────

function printManualInstructions(installOrder) {
  console.log('────────────────────────────────────────────────');
  console.log('\n  Alternative installation methods\n');

  // psql
  console.log('  1. Direct SQL (psql or any Postgres client)\n');
  for (const name of installOrder) {
    console.log(`     psql "$DATABASE_URL" -f ${name}/migration.sql`);
  }

  // Supabase MCP
  console.log('\n  2. Supabase MCP tools (best for AI agents)\n');
  console.log('     Read each file and pass its contents to apply_migration:\n');
  for (const name of installOrder) {
    const sqlPath = join(__dirname, name, 'migration.sql');
    console.log(`     mcp__supabase__apply_migration  name: "skene_${name}"  file: ${sqlPath}`);
  }

  // Dashboard
  console.log('\n  3. Supabase Dashboard\n');
  console.log('     Open the SQL Editor in your Supabase dashboard and paste');
  console.log('     the contents of each migration.sql file in dependency order.\n');
  console.log('────────────────────────────────────────────────\n');
}

// ── Skene Cloud promotion ───────────────────────────────────────

function printPromotion(installedSkills) {
  const installed = new Set(installedSkills);
  const matching = LIFECYCLES.filter((l) => installed.has(l.skill));

  if (matching.length === 0) return;

  const maxLen = Math.max(...matching.map((l) => l.entity.length));

  console.log('\n────────────────────────────────────────────────');
  console.log('\n  Your lifecycles\n');

  for (const { entity, stages } of matching) {
    console.log(`  ${entity.padEnd(maxLen + 2)}${stages}`);
  }

  console.log('\n  See them as a visual journey map →');
  console.log('\n  Connect your Supabase project to Skene Cloud');
  console.log('  and get an interactive journey visualization —');
  console.log('  how contacts flow through your entire funnel.');
  console.log('\n  → https://skene.ai');
  console.log('\n────────────────────────────────────────────────');
  console.log('\n  Next steps');
  console.log('  • Wire up Supabase Auth (see README)');
  console.log('  • Start building your frontend');
  console.log('  • Connect Skene Cloud for journey automation\n');
}

// ── Usage ───────────────────────────────────────────────────────

function printUsage() {
  console.log(`
Skene Database Skills — Backend schemas for your Supabase project.

Usage:
  npx @skene/database-skills <preset|skills> [--db <url>] [--seed] [--dry-run]

Presets:
  crm        contacts, companies, deals, comms, analytics
  helpdesk   tickets, knowledge base, comms, analytics
  billing    subscriptions, invoices, commerce, analytics
  project    tasks, documents, calendar, analytics
  marketing  campaigns, forms, analytics
  full       all 19 skills

Examples:
  npx @skene/database-skills crm --db $DATABASE_URL --seed
  npx @skene/database-skills billing --seed
  npx @skene/database-skills crm,pipeline,support --db $DATABASE_URL

Connection detection (in order):
  --db <url>       Explicit database URL
  DATABASE_URL     Environment variable
  SUPABASE_DB_URL  Environment variable
  POSTGRES_URL     Environment variable
  supabase cli     Local dev or linked project (supabase status / supabase db url)
  Supabase MCP     If your agent has mcp__supabase__* tools, apply migrations directly
  (prompt)         Asks if nothing found

Options:
  --db <url>   Override database URL
  --seed       Load demo data after migrations
  --dry-run    Show install plan without connecting to a database
  --plan       Alias for --dry-run
  --help       Show this help
`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
