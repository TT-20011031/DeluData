import { describe, expect, it } from 'vitest'

import { splitSemanticTerms } from './semanticTerms'

describe('splitSemanticTerms', () => {
    it('splits Chinese and English semantic term separators', () => {
        expect(splitSemanticTerms('销售额、订单销售额，销售总额, 本币销售额')).toEqual([
            '销售额',
            '订单销售额',
            '销售总额',
            '本币销售额',
        ])
    })
})
