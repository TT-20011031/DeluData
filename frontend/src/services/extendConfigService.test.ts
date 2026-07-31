import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function jsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
    })
}

describe('extendConfigService semantic access policies', () => {
    beforeEach(() => {
        vi.resetModules()
        vi.doMock('@/config', () => ({ API_BASE_URL: '/api' }))
        vi.doMock('@/stores/authStore', () => ({
            getAuthHeader: () => ({ Authorization: 'Bearer test-token' }),
        }))
    })

    afterEach(() => {
        vi.unstubAllGlobals()
        vi.doUnmock('@/config')
        vi.doUnmock('@/stores/authStore')
    })

    it('parses natural language without creating or activating a policy', async () => {
        const suggestion = {
            draft_patch: {
                subject: { type: 'user', id: 'test1' },
                tables: [{ table_id: 1, decision: 'visible', hidden_column_ids: [], hidden_metric_ids: [] }],
            },
            validation: { blockers: [], warnings: [] },
            summary: { effect_count: 1 },
        }
        const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(suggestion))
        vi.stubGlobal('fetch', fetchMock)
        const { extendConfigService } = await import('@/services/extendConfigService')

        const result = await extendConfigService.parseSemanticAccessNaturalLanguage('test1 只能查看本部门订单')

        expect(result).toEqual(suggestion)
        expect(fetchMock).toHaveBeenCalledWith(
            '/api/config/semantic/access-policies/parse-natural-language',
            expect.objectContaining({ method: 'POST' }),
        )
        const request = fetchMock.mock.calls[0][1] as RequestInit
        expect(JSON.parse(String(request.body))).toEqual({ source_text: 'test1 只能查看本部门订单' })
    })

    it('creates only a structured permission draft', async () => {
        const policy = { id: 12, name: 'test1 的问数权限', status: 'draft' }
        const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(policy))
        vi.stubGlobal('fetch', fetchMock)
        const { extendConfigService } = await import('@/services/extendConfigService')

        await extendConfigService.createSemanticAccessPolicyDraft({
            subject: { type: 'user', id: '1' },
            tables: [{ table_id: 1, decision: 'visible', hidden_column_ids: [3], hidden_metric_ids: [] }],
        })

        const request = fetchMock.mock.calls[0][1] as RequestInit
        expect(JSON.parse(String(request.body))).toEqual({
            subject: { type: 'user', id: '1' },
            tables: [{ table_id: 1, decision: 'visible', hidden_column_ids: [3], hidden_metric_ids: [] }],
        })
    })

    it('creates a draft and automatically activates the returned policy id', async () => {
        const draft = { id: 41, name: 'test1 的问数权限', status: 'draft' }
        const active = { ...draft, status: 'active' }
        const fetchMock = vi.fn<typeof fetch>()
            .mockResolvedValueOnce(jsonResponse(draft))
            .mockResolvedValueOnce(jsonResponse(active))
        vi.stubGlobal('fetch', fetchMock)
        const { extendConfigService } = await import('@/services/extendConfigService')

        const result = await extendConfigService.saveAndActivateSemanticAccessPolicy({
            subject: { type: 'user', id: 'test1' },
            tables: [{ table_id: 1, decision: 'visible', hidden_column_ids: [], hidden_metric_ids: [] }],
        })

        expect(result.active).toEqual(active)
        expect(fetchMock).toHaveBeenNthCalledWith(
            2,
            '/api/config/semantic/access-policies/41/activate',
            expect.objectContaining({ method: 'POST' }),
        )
    })

    it('updates a draft and automatically activates the id returned by update', async () => {
        const draft = { id: 52, name: '角色权限', status: 'draft' }
        const fetchMock = vi.fn<typeof fetch>()
            .mockResolvedValueOnce(jsonResponse(draft))
            .mockResolvedValueOnce(jsonResponse({ ...draft, status: 'active' }))
        vi.stubGlobal('fetch', fetchMock)
        const { extendConfigService } = await import('@/services/extendConfigService')

        await extendConfigService.saveAndActivateSemanticAccessPolicy({
            subject: { type: 'role', id: '7' },
            tables: [],
        }, 12)

        expect(fetchMock).toHaveBeenNthCalledWith(
            1,
            '/api/config/semantic/access-policies/12',
            expect.objectContaining({ method: 'PATCH' }),
        )
        expect(fetchMock).toHaveBeenNthCalledWith(
            2,
            '/api/config/semantic/access-policies/52/activate',
            expect.objectContaining({ method: 'POST' }),
        )
    })

    it('keeps the persisted draft and detailed blocker error when activation fails', async () => {
        const draft = { id: 12, name: 'test1 的问数权限', status: 'draft' }
        const fetchMock = vi.fn<typeof fetch>()
            .mockResolvedValueOnce(jsonResponse(draft))
            .mockResolvedValueOnce(jsonResponse({
                detail: {
                    error_type: 'policy_blocked',
                    message: '策略仍有阻断项，无法启用',
                    details: { blockers: [{ code: 'invalid_column', message: '归属人字段不存在' }] },
                },
            }, 409))
        vi.stubGlobal('fetch', fetchMock)
        const { extendConfigService, SemanticAccessApiError, SemanticAccessSaveError } = await import('@/services/extendConfigService')

        const error = await extendConfigService.saveAndActivateSemanticAccessPolicy({
            subject: { type: 'user', id: 'test1' },
            tables: [],
        }).catch(item => item)

        expect(error).toBeInstanceOf(SemanticAccessSaveError)
        expect(error.persistedPolicy).toEqual(draft)
        expect(error.cause).toBeInstanceOf(SemanticAccessApiError)
        expect(error.cause).toMatchObject({
            status: 409,
            errorType: 'policy_blocked',
            details: { blockers: [{ code: 'invalid_column', message: '归属人字段不存在' }] },
        })
    })
})
