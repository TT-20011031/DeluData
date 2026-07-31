import http from 'node:http'

const port = 4176
let runtimeMode = 'trusted'
let applied = true
let governanceObserveOnly = true
let governanceRunId = 2088
const reviewedAssets = new Set()
const decoratedAssetKeys = new Set()

const governanceCandidates = [
    {
        candidate_id: 901, run_id: 2087, target_type: 'columns', target_id: 12, candidate_type: 'mark_sensitive',
        title: '标记敏感字段：客户手机号', before: { is_sensitive: false }, proposed_patch: { is_sensitive: true }, applied_patch: {},
        supporting_evidence: [{ source_type: 'aggregate_profile' }, { source_type: 'deterministic_validation' }], conflicting_evidence: [],
        source_types: ['aggregate_profile', 'deterministic_validation'], score: 0.9875, score_version: 'evidence-v1', risk_level: 'high',
        status: 'needs_review', auto_eligible: true, deterministic_check_passed: true, policy_version: 'evidence-v1', decision_reason: null,
        decided_by: null, decided_at: null, applied_at: null, created_at: '2026-06-24T09:30:00+08:00', updated_at: '2026-06-24T09:30:00+08:00',
    },
    {
        candidate_id: 902, run_id: 2087, target_type: 'metrics', target_id: 44, candidate_type: 'metric',
        title: '指标建议：有效订单金额', before: { status: 'suggested', is_queryable: false }, proposed_patch: { status: 'confirmed', is_queryable: true }, applied_patch: {},
        supporting_evidence: [{ source_type: 'golden_sql' }, { source_type: 'deterministic_validation' }], conflicting_evidence: [{ source_type: 'runtime_pattern' }],
        source_types: ['deterministic_validation', 'golden_sql'], score: 0.72, score_version: 'evidence-v1', risk_level: 'high',
        status: 'blocked', auto_eligible: false, deterministic_check_passed: true, policy_version: 'evidence-v1', decision_reason: null,
        decided_by: null, decided_at: null, applied_at: null, created_at: '2026-06-24T09:31:00+08:00', updated_at: '2026-06-24T09:31:00+08:00',
    },
    {
        candidate_id: 903, run_id: 2087, target_type: 'tables', target_id: 7, candidate_type: 'business_semantics',
        title: '业务语义建议：销售订单', before: { business_name: 'sales_order' }, proposed_patch: { business_name: '销售订单', synonyms: ['订单', '销售单'] }, applied_patch: {},
        supporting_evidence: [{ source_type: 'golden_sql' }, { source_type: 'runtime_pattern' }], conflicting_evidence: [],
        source_types: ['golden_sql', 'runtime_pattern'], score: 0.97, score_version: 'evidence-v1', risk_level: 'medium',
        status: 'needs_review', auto_eligible: false, deterministic_check_passed: false, policy_version: 'evidence-v1', decision_reason: null,
        decided_by: null, decided_at: null, applied_at: null, created_at: '2026-06-24T09:32:00+08:00', updated_at: '2026-06-24T09:32:00+08:00',
    },
]

const governanceRun = () => ({
    run_id: governanceRunId, status: 'completed', stage: 'completed', progress: 100, trigger_type: 'schema_apply', scan_id: 1042,
    schema_fingerprint: 'sha256:f72de91c', observe_only: governanceObserveOnly,
    summary: { schema_facts: 597, profiled_tables: 20, profiled_columns: 326, failed_tables: 2, runtime_facts: 142, candidates_generated: 3, auto_applied: 0, conflicts: 1 },
    budget: { max_tables_per_run: 20, max_run_seconds: 300 }, error_message: null,
    created_at: '2026-06-24T09:25:00+08:00', started_at: '2026-06-24T09:25:02+08:00', completed_at: '2026-06-24T09:29:48+08:00',
})

const diffItems = [
    { id: 'd1', change_type: 'added', object_type: 'table', physical_identity: 'sales_region_dim', severity: 'info', blocking: false },
    { id: 'd2', change_type: 'added', object_type: 'column', physical_identity: 'sales_order.region_code', severity: 'info', blocking: false },
    { id: 'd3', change_type: 'modified', object_type: 'column', physical_identity: 'sales_order.amount', severity: 'critical', blocking: true },
    { id: 'd4', change_type: 'modified', object_type: 'column', physical_identity: 'customer.customer_name', severity: 'info', blocking: false },
    { id: 'd5', change_type: 'removed', object_type: 'column', physical_identity: 'inventory.legacy_stock', severity: 'critical', blocking: true },
    { id: 'd6', change_type: 'removed', object_type: 'table', physical_identity: 'orders_backup_2024', severity: 'critical', blocking: true },
]

const scan = () => ({
    scan_id: 1042,
    status: applied ? 'applied' : 'previewed',
    base_fingerprint: 'sha256:8b17c8af',
    target_fingerprint: 'sha256:f72de91c',
    created_at: '2026-06-24T10:20:00+08:00',
    expires_at: '2026-06-25T10:20:00+08:00',
    applied_at: applied ? '2026-06-24T10:24:00+08:00' : null,
    summary: { total: 11, added: 6, modified: 3, removed: 2, blocking: 3 },
    diff_items: diffItems,
    affected_assets: [
        { asset_type: 'metric', asset_name: 'GMV', reason: 'Depends on sales_order.amount' },
        { asset_type: 'relationship', asset_name: 'Inventory relation', reason: 'Depends on inventory.legacy_stock' },
        { asset_type: 'column', asset_name: 'Legacy stock', reason: 'Physical column removed' },
    ],
    blocking_changes: diffItems.filter(item => item.blocking),
})

function sendJson(response, status, value) {
    const body = JSON.stringify(value)
    response.writeHead(status, {
        'content-type': 'application/json; charset=utf-8',
        'content-length': Buffer.byteLength(body),
    })
    response.end(body)
}

function readBody(request) {
    return new Promise(resolve => {
        const chunks = []
        request.on('data', chunk => chunks.push(chunk))
        request.on('end', () => resolve(Buffer.concat(chunks)))
    })
}

function upstreamHeaders(request, body, acceptJson = false) {
    const headers = { ...request.headers, host: '127.0.0.1:8031' }
    delete headers['content-length']
    if (acceptJson) delete headers['accept-encoding']
    if (body.length) headers['content-length'] = String(body.length)
    return headers
}

function proxyRequest(request, response, body) {
    const upstream = http.request({
        hostname: '127.0.0.1',
        port: 8031,
        path: request.url,
        method: request.method,
        headers: upstreamHeaders(request, body),
    }, upstreamResponse => {
        response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers)
        upstreamResponse.pipe(response)
    })
    upstream.on('error', error => sendJson(response, 502, { detail: error.message }))
    if (body.length) upstream.write(body)
    upstream.end()
}

function decorateAsset(collection, asset, syncState, reason) {
    if (!asset) return
    const key = `${collection}:${asset.id}`
    decoratedAssetKeys.add(key)
    if (reviewedAssets.has(key)) return
    asset.sync_state = syncState
    asset.is_queryable = false
    asset.stale_reason_json = { reason, scan_id: 1042 }
    asset.last_seen_scan_id = 1042
}

function decorateCatalog(payload) {
    const firstQueryable = collection => collection?.find(item => item.status === 'confirmed' && item.is_queryable) || collection?.[0]
    decorateAsset('columns', firstQueryable(payload.columns), 'orphaned', 'physical_column_removed')
    decorateAsset('metrics', firstQueryable(payload.metrics), 'stale', 'schema_dependency_changed')
    decorateAsset('relationships', firstQueryable(payload.relationships), 'stale', 'schema_dependency_changed')
    return payload
}

function proxyCatalog(request, response, body) {
    const upstream = http.request({
        hostname: '127.0.0.1',
        port: 8031,
        path: request.url,
        method: request.method,
        headers: upstreamHeaders(request, body, true),
    }, upstreamResponse => {
        const chunks = []
        upstreamResponse.on('data', chunk => chunks.push(chunk))
        upstreamResponse.on('end', () => {
            const raw = Buffer.concat(chunks).toString('utf8')
            if ((upstreamResponse.statusCode || 500) >= 400) {
                response.writeHead(upstreamResponse.statusCode || 500, { 'content-type': upstreamResponse.headers['content-type'] || 'application/json' })
                return response.end(raw)
            }
            try {
                sendJson(response, upstreamResponse.statusCode || 200, decorateCatalog(JSON.parse(raw)))
            } catch (error) {
                sendJson(response, 502, { detail: `Preview catalog transform failed: ${error.message}` })
            }
        })
    })
    upstream.on('error', error => sendJson(response, 502, { detail: error.message }))
    if (body.length) upstream.write(body)
    upstream.end()
}

const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1')
    const path = url.pathname

    if (request.method === 'GET' && path === '/api/config/semantic/readiness') {
        const remaining = decoratedAssetKeys.size ? [...decoratedAssetKeys].filter(key => !reviewedAssets.has(key)).length : 3
        return sendJson(response, 200, {
            status: remaining ? 'partially_ready' : 'ready',
            runtime_mode: runtimeMode,
            structure: { connected: true, has_applied_scan: true, pending_drift: false },
            semantics: { queryable_tables: 34, queryable_columns: 537, confirmed_metrics: 60, confirmed_relationships: 6, stale_assets: remaining },
            security: { readonly_configured: true, blocking: false },
            governance: {
                last_run: governanceRun(), evidence_freshness: 'fresh', profile_coverage: 0.73,
                pending_candidates: governanceCandidates.filter(item => ['proposed', 'needs_review', 'blocked'].includes(item.status)).length,
                high_risk_candidates: governanceCandidates.filter(item => ['proposed', 'needs_review', 'blocked'].includes(item.status) && ['high', 'critical'].includes(item.risk_level)).length,
                recent_auto_applied: governanceCandidates.filter(item => item.status === 'auto_applied').length,
            },
            blockers: remaining ? [`存在 ${remaining} 个过期或失联语义资产`] : [],
        })
    }
    if (request.method === 'GET' && path === '/api/config/semantic/governance/runs') {
        return sendJson(response, 200, { runs: [governanceRun()] })
    }
    if (request.method === 'POST' && path === '/api/config/semantic/governance/runs') {
        await readBody(request)
        governanceRunId += 1
        return sendJson(response, 202, { ...governanceRun(), status: 'pending', stage: 'queued', progress: 0, trigger_type: 'manual' })
    }
    if (request.method === 'GET' && path === '/api/config/semantic/governance/candidates') {
        const minScore = Number(url.searchParams.get('min_score') || 0)
        return sendJson(response, 200, { candidates: governanceCandidates.filter(item => item.score >= minScore) })
    }
    if (request.method === 'GET' && path === '/api/config/semantic/governance/policy') {
        return sendJson(response, 200, {
            id: 1, enabled: true, observe_only: governanceObserveOnly, run_after_scan: true,
            exact_row_threshold: 50000, sample_row_limit: 10000, table_timeout_sec: 5, max_tables_per_run: 20, max_run_seconds: 300,
            review_threshold: 0.65, high_confidence_threshold: 0.9, auto_apply_threshold: 0.98, min_auto_evidence_sources: 2,
            auto_action_types: ['mark_sensitive', 'disable_invalid_asset'], policy_version: 'evidence-v1', updated_at: '2026-06-24T09:00:00+08:00',
        })
    }
    if (request.method === 'PATCH' && path === '/api/config/semantic/governance/policy') {
        const raw = await readBody(request)
        governanceObserveOnly = JSON.parse(raw.toString() || '{}').observe_only ?? governanceObserveOnly
        return sendJson(response, 200, {
            id: 1, enabled: true, observe_only: governanceObserveOnly, run_after_scan: true,
            exact_row_threshold: 50000, sample_row_limit: 10000, table_timeout_sec: 5, max_tables_per_run: 20, max_run_seconds: 300,
            review_threshold: 0.65, high_confidence_threshold: 0.9, auto_apply_threshold: 0.98, min_auto_evidence_sources: 2,
            auto_action_types: ['mark_sensitive', 'disable_invalid_asset'], policy_version: 'evidence-v1', updated_at: new Date().toISOString(),
        })
    }
    const evidenceMatch = path.match(/^\/api\/config\/semantic\/governance\/evidence\/([^/]+)\/(\d+)$/)
    if (request.method === 'GET' && evidenceMatch) {
        return sendJson(response, 200, { evidence: [
            { evidence_id: 7001, run_id: 2087, subject_type: evidenceMatch[1].replace(/s$/, ''), subject_id: Number(evidenceMatch[2]), claim_type: 'aggregate_profile', claim_key: 'profile', value: { distinct_ratio: 0.88 }, source_type: 'aggregate_profile', source_ref: 'profile:441', direction: 'support', reliability: 0.75, strength: 1, observed_at: '2026-06-24T09:28:00+08:00', expires_at: '2026-07-01T09:28:00+08:00' },
            { evidence_id: 7002, run_id: 2087, subject_type: evidenceMatch[1].replace(/s$/, ''), subject_id: Number(evidenceMatch[2]), claim_type: 'runtime_usage', claim_key: 'runtime', value: { total: 48 }, source_type: 'runtime_pattern', source_ref: 'query-runs:30d', direction: 'support', reliability: 0.7, strength: 1, observed_at: '2026-06-24T09:28:30+08:00', expires_at: '2026-07-24T09:28:30+08:00' },
        ] })
    }
    const candidateDecisionMatch = path.match(/^\/api\/config\/semantic\/governance\/candidates\/(\d+)\/(accept|reject|rollback)$/)
    if (request.method === 'POST' && candidateDecisionMatch) {
        await readBody(request)
        const candidate = governanceCandidates.find(item => item.candidate_id === Number(candidateDecisionMatch[1]))
        if (!candidate) return sendJson(response, 404, { detail: '候选不存在' })
        candidate.status = candidateDecisionMatch[2] === 'accept' ? 'accepted' : candidateDecisionMatch[2] === 'reject' ? 'rejected' : 'rolled_back'
        candidate.decided_at = new Date().toISOString()
        return sendJson(response, 200, candidate)
    }
    if (request.method === 'POST' && path === '/api/config/semantic/governance/candidates/batch') {
        const raw = await readBody(request)
        const payload = JSON.parse(raw.toString() || '{}')
        const ids = Array.isArray(payload.ids) ? payload.ids : []
        governanceCandidates.filter(item => ids.includes(item.candidate_id)).forEach(item => { item.status = payload.action === 'accept' ? 'accepted' : 'rejected' })
        return sendJson(response, 200, { succeeded: ids, failed: [] })
    }
    if (request.method === 'GET' && path === '/api/config/semantic/scans') {
        return sendJson(response, 200, { scans: [scan()], total: 1 })
    }
    if (request.method === 'GET' && path === '/api/config/semantic/scans/1042') {
        return sendJson(response, 200, scan())
    }
    if (request.method === 'POST' && path === '/api/config/semantic/scans/preview') {
        await readBody(request)
        return sendJson(response, 200, scan())
    }
    if (request.method === 'POST' && path === '/api/config/semantic/scans/1042/apply') {
        await readBody(request)
        applied = true
        return sendJson(response, 200, { ...scan(), idempotent: false, applied_changes: 11 })
    }
    if (request.method === 'PATCH' && path === '/api/config/semantic/runtime-mode') {
        const raw = await readBody(request)
        try {
            runtimeMode = JSON.parse(raw.toString() || '{}').runtime_mode || runtimeMode
        } catch {}
        return sendJson(response, 200, { runtime_mode: runtimeMode, warning: runtimeMode === 'shadow' ? 'Shadow mode may nearly double query load.' : null })
    }
    if (request.method === 'POST' && path === '/api/config/semantic/readiness/check-question') {
        await readBody(request)
        return sendJson(response, 200, {
            answerable: true,
            status: 'ready',
            error_type: null,
            blockers: [],
            required_actions: [],
            referenced_objects: { table_ids: [1], metric_ids: [1], relationship_ids: [] },
        })
    }

    const reviewMatch = path.match(/^\/api\/config\/semantic\/(tables|columns|metrics|relationships)\/(\d+)$/)
    if (request.method === 'PATCH' && reviewMatch) {
        await readBody(request)
        reviewedAssets.add(`${reviewMatch[1]}:${reviewMatch[2]}`)
        return sendJson(response, 200, { ok: true, preview: true })
    }

    const body = await readBody(request)
    if (request.method === 'GET' && path === '/api/config/semantic/models') {
        return proxyCatalog(request, response, body)
    }
    return proxyRequest(request, response, body)
})

server.listen(port, '0.0.0.0', () => {
    process.stdout.write(`DeluData trust preview proxy listening on ${port}\n`)
})
