/**
 * 题库管理 Tab
 *
 * 布局：
 *   ┌─ AI 出题 卡片（顶部，虚线边框）
 *   ├─ 本页子 Tab：  [题库列表]  [手动出题]
 *   │    - 题库列表：table 展示已有题目，支持编辑/删除
 *   │    - 手动出题：新建/编辑表单
 *   └─ 删除确认弹窗
 */
import { useEffect, useState, useCallback } from 'react';
import { Plus, Minus, Pencil, Trash2, Sparkles, Loader2, List, FilePlus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { useToast } from '@/components/ui/toast';
import { scienceAdminService } from '@/services/scienceAdminService';
import type { ScienceQuizQuestion, ScienceQuizQuestionUpsertRequest, QuizGenerateRequest } from '@/types/scienceAdmin';

const ANSWER_LETTERS = ['A', 'B', 'C', 'D', 'E', 'F'];
const DIFFICULTY_LABELS: Record<number, string> = { 1: '简单', 2: '较易', 3: '中等', 4: '较难', 5: '困难' };

type SubTab = 'list' | 'form';

const EMPTY_FORM: ScienceQuizQuestionUpsertRequest = {
    question_text: '',
    options: ['', ''],
    answer_key: 'A',
    explanation: '',
    difficulty: 1,
    source_type: 'bank',
    is_active: true,
};

export default function QuizTab() {
    const { toast } = useToast();
    const [questions, setQuestions] = useState<ScienceQuizQuestion[]>([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [subTab, setSubTab] = useState<SubTab>('list');
    const [form, setForm] = useState<ScienceQuizQuestionUpsertRequest>({ ...EMPTY_FORM, options: ['', ''] });
    const [editingId, setEditingId] = useState<string | null>(null);

    const [confirmOpen, setConfirmOpen] = useState(false);
    const [confirmTarget, setConfirmTarget] = useState<ScienceQuizQuestion | null>(null);

    // AI 出题
    const [aiForm, setAiForm] = useState<QuizGenerateRequest>({ topic: '', count: 5, difficulty: 2 });
    const [generating, setGenerating] = useState(false);

    const loadQuestions = useCallback(async () => {
        setLoading(true);
        try {
            setQuestions(await scienceAdminService.listQuizQuestions());
        } catch (err) {
            toast({ type: 'error', title: '加载题库失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { void loadQuestions(); }, [loadQuestions]);

    // ========== AI 出题 ==========
    const handleGenerate = async () => {
        if (!aiForm.topic.trim()) {
            toast({ type: 'warning', title: '请输入出题主题' });
            return;
        }
        setGenerating(true);
        try {
            const result = await scienceAdminService.generateQuizQuestions(aiForm);
            toast({ type: 'success', title: `AI 已生成 ${result.generated_count} 道题目` });
            setSubTab('list');
            await loadQuestions();
        } catch (err) {
            toast({ type: 'error', title: 'AI 出题失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setGenerating(false);
        }
    };

    // ========== 手动出题 ==========
    const handleEdit = (q: ScienceQuizQuestion) => {
        setEditingId(q.id);
        setForm({
            question_text: q.question_text,
            options: [...q.options],
            answer_key: q.answer_key,
            explanation: q.explanation ?? '',
            difficulty: q.difficulty,
            source_type: q.source_type,
            is_active: q.is_active,
        });
        setSubTab('form');
    };

    const handleCancel = () => {
        setEditingId(null);
        setForm({ ...EMPTY_FORM, options: ['', ''] });
        setSubTab('list');
    };

    const addOption = () => {
        if (form.options.length >= 6) return;
        setForm((s) => ({ ...s, options: [...s.options, ''] }));
    };

    const removeOption = (idx: number) => {
        if (form.options.length <= 2) return;
        setForm((s) => {
            const opts = s.options.filter((_, i) => i !== idx);
            const answerIdx = ANSWER_LETTERS.indexOf(s.answer_key);
            const newKey = answerIdx >= opts.length ? 'A' : s.answer_key;
            return { ...s, options: opts, answer_key: newKey };
        });
    };

    const updateOption = (idx: number, value: string) => {
        setForm((s) => {
            const opts = [...s.options];
            opts[idx] = value;
            return { ...s, options: opts };
        });
    };

    const handleSave = async () => {
        if (!form.question_text.trim()) {
            toast({ type: 'warning', title: '请填写题干' });
            return;
        }
        const validOptionsWithIndex = form.options
            .map((opt, idx) => ({ text: opt.trim(), originalChar: ANSWER_LETTERS[idx] }))
            .filter((item) => item.text);

        if (validOptionsWithIndex.length < 2) {
            toast({ type: 'warning', title: '至少需要 2 个有效选项' });
            return;
        }

        const answerExistsIndex = validOptionsWithIndex.findIndex((o) => o.originalChar === form.answer_key);
        const mappedAnswerKey = answerExistsIndex !== -1 ? ANSWER_LETTERS[answerExistsIndex] : 'A';

        setBusy(true);
        try {
            const payload = {
                ...form,
                options: validOptionsWithIndex.map((o) => o.text),
                answer_key: mappedAnswerKey,
            };
            if (editingId) {
                await scienceAdminService.updateQuizQuestion(editingId, payload);
            } else {
                await scienceAdminService.createQuizQuestion(payload);
            }
            toast({ type: 'success', title: editingId ? '题目已更新' : '题目已创建' });
            handleCancel();
            await loadQuestions();
        } catch (err) {
            toast({ type: 'error', title: '保存失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setBusy(false);
        }
    };

    // ========== 删除 ==========
    const handleDelete = async () => {
        if (!confirmTarget) return;
        setBusy(true);
        try {
            await scienceAdminService.deleteQuizQuestion(confirmTarget.id);
            toast({ type: 'success', title: '题目已删除' });
            setConfirmOpen(false);
            setConfirmTarget(null);
            if (editingId === confirmTarget.id) handleCancel();
            await loadQuestions();
        } catch (err) {
            toast({ type: 'error', title: '删除失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setBusy(false);
        }
    };

    if (loading) {
        return <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10"><CardContent className="py-10 text-center text-manus-muted">加载题库中...</CardContent></Card>;
    }

    return (
        <div className="space-y-6">
            {/* ========== AI 出题卡片 ========== */}
            <Card className="bg-manus-secondary border border-dashed border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
                <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Sparkles className="h-5 w-5 text-accent" />
                        AI 智能出题
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                        <div className="space-y-2 md:col-span-2">
                            <Label>出题主题 <span className="text-red-400">*</span></Label>
                            <Input
                                placeholder="如：太阳系行星、恐龙知识、海洋生物"
                                value={aiForm.topic}
                                onChange={(e) => setAiForm((s) => ({ ...s, topic: e.target.value }))}
                                disabled={generating}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>数量 / 难度</Label>
                            <div className="flex gap-2">
                                <Select value={String(aiForm.count)} onValueChange={(v) => setAiForm((s) => ({ ...s, count: Number(v) }))}>
                                    <SelectTrigger className="w-20"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {[1, 2, 3, 5, 8, 10].map((n) => (
                                            <SelectItem key={n} value={String(n)}>{n} 题</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <Select value={String(aiForm.difficulty)} onValueChange={(v) => setAiForm((s) => ({ ...s, difficulty: Number(v) }))}>
                                    <SelectTrigger className="flex-1"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {Object.entries(DIFFICULTY_LABELS).map(([k, label]) => (
                                            <SelectItem key={k} value={k}>{label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <Button onClick={() => void handleGenerate()} disabled={generating} className="h-10 bg-black text-white hover:bg-black/90 shadow-sm shadow-black/20">
                            {generating ? (
                                <><Loader2 className="h-4 w-4 mr-2 animate-spin" />AI 生成中...</>
                            ) : (
                                <><Sparkles className="h-4 w-4 mr-2" />生成并保存</>
                            )}
                        </Button>
                    </div>
                </CardContent>
            </Card>

            {/* ========== 本页子 Tab ========== */}
            <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
                <CardHeader className="pb-2 border-b border-manus-border/50">
                    <div className="flex items-center gap-1">
                        <button
                            onClick={() => { handleCancel(); setSubTab('list'); }}
                            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${subTab === 'list'
                                    ? 'border-black bg-black text-white shadow-sm'
                                    : 'border-transparent text-manus-text hover:bg-manus-tertiary'
                                }`}
                        >
                            <List className="h-4 w-4" />
                            题库列表
                            <Badge variant="outline" className={`ml-1 text-xs ${subTab === 'list' ? 'border-white/30 text-white' : 'border-manus-border text-manus-text'}`}>{questions.length}</Badge>
                        </button>
                        <button
                            onClick={() => { setEditingId(null); setForm({ ...EMPTY_FORM, options: ['', ''] }); setSubTab('form'); }}
                            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${subTab === 'form'
                                    ? 'border-black bg-black text-white shadow-sm'
                                    : 'border-transparent text-manus-text hover:bg-manus-tertiary'
                                }`}
                        >
                            <FilePlus className="h-4 w-4" />
                            {editingId ? '编辑题目' : '手动出题'}
                        </button>
                    </div>
                </CardHeader>

                <CardContent>
                    {/* ===== 子 Tab: 题库列表 ===== */}
                    {subTab === 'list' && (
                        <div className="overflow-auto rounded-lg border border-manus-border">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="bg-manus-tertiary text-manus-muted">
                                        <th className="text-left py-3 px-4 font-medium">题干</th>
                                        <th className="text-left py-3 px-4 font-medium w-16">答案</th>
                                        <th className="text-left py-3 px-4 font-medium w-16">难度</th>
                                        <th className="text-left py-3 px-4 font-medium w-16">来源</th>
                                        <th className="text-left py-3 px-4 font-medium w-24">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {questions.length === 0 && (
                                        <tr><td colSpan={5} className="py-8 text-center text-manus-muted">暂无题目，点击「手动出题」或使用 AI 出题</td></tr>
                                    )}
                                    {questions.map((q) => (
                                        <tr key={q.id} className="border-t border-manus-border hover:bg-manus-tertiary/50 transition-colors">
                                            <td className="py-3 px-4">
                                                <p className="line-clamp-1 font-medium">{q.question_text}</p>
                                                <p className="text-xs text-manus-muted mt-0.5 line-clamp-1">
                                                    {q.options.map((o, i) => `${ANSWER_LETTERS[i]}.${o}`).join('  ')}
                                                </p>
                                            </td>
                                            <td className="py-3 px-4 font-bold text-center">{q.answer_key}</td>
                                            <td className="py-3 px-4 text-center">
                                                <Badge variant="outline" className="border-manus-border text-xs">{DIFFICULTY_LABELS[q.difficulty] || q.difficulty}</Badge>
                                            </td>
                                            <td className="py-3 px-4 text-center text-xs text-manus-muted">{q.source_type === 'bank' ? '原创' : 'AI'}</td>
                                            <td className="py-3 px-4">
                                                <div className="flex gap-1">
                                                    <Button size="sm" variant="outline" onClick={() => handleEdit(q)} className="h-7 px-2 bg-manus-tertiary border-manus-border">
                                                        <Pencil className="h-3 w-3 mr-1" />编辑
                                                    </Button>
                                                    <Button size="sm" variant="destructive" onClick={() => { setConfirmTarget(q); setConfirmOpen(true); }} className="h-7 px-2">
                                                        <Trash2 className="h-3 w-3 mr-1" />删除
                                                    </Button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* ===== 子 Tab: 手动出题 / 编辑 ===== */}
                    {subTab === 'form' && (
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label>题干 <span className="text-red-400">*</span></Label>
                                <Input
                                    placeholder="请输入题目内容"
                                    value={form.question_text}
                                    onChange={(e) => setForm((s) => ({ ...s, question_text: e.target.value }))}
                                />
                            </div>

                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <Label>选项 <span className="text-red-400">*</span> <span className="text-manus-muted text-xs">（至少 2 个）</span></Label>
                                    <Button size="sm" variant="outline" onClick={addOption} disabled={form.options.length >= 6} className="h-7 px-2 bg-manus-tertiary border-manus-border">
                                        <Plus className="h-3 w-3 mr-1" />添加选项
                                    </Button>
                                </div>
                                <div className="space-y-2">
                                    {form.options.map((opt, idx) => (
                                        <div key={idx} className="flex items-center gap-2">
                                            <span className="w-6 text-center text-sm font-bold text-manus-muted">{ANSWER_LETTERS[idx]}.</span>
                                            <Input
                                                placeholder={`选项 ${ANSWER_LETTERS[idx]}`}
                                                value={opt}
                                                onChange={(e) => updateOption(idx, e.target.value)}
                                                className="flex-1"
                                            />
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => removeOption(idx)}
                                                disabled={form.options.length <= 2}
                                                className="h-8 w-8 p-0 bg-manus-tertiary border-manus-border"
                                            >
                                                <Minus className="h-3 w-3" />
                                            </Button>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>正确答案</Label>
                                    <Select value={form.answer_key} onValueChange={(v) => setForm((s) => ({ ...s, answer_key: v }))}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {form.options.map((_, idx) => (
                                                <SelectItem key={idx} value={ANSWER_LETTERS[idx]}>{ANSWER_LETTERS[idx]}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>难度</Label>
                                    <Select value={String(form.difficulty)} onValueChange={(v) => setForm((s) => ({ ...s, difficulty: Number(v) }))}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {[1, 2, 3, 4, 5].map((d) => (
                                                <SelectItem key={d} value={String(d)}>{d} - {DIFFICULTY_LABELS[d]}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>来源类型</Label>
                                    <Select value={form.source_type} onValueChange={(v) => setForm((s) => ({ ...s, source_type: v as 'bank' | 'generated' }))}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="bank">题库原创</SelectItem>
                                            <SelectItem value="generated">AI 生成</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label>解析 <span className="text-manus-muted text-xs">(可选)</span></Label>
                                <Textarea
                                    placeholder="答案解析说明"
                                    value={form.explanation ?? ''}
                                    onChange={(e) => setForm((s) => ({ ...s, explanation: e.target.value }))}
                                    className="border border-manus-border min-h-[80px]"
                                />
                            </div>

                            <div className="flex justify-end gap-2">
                                <Button variant="outline" onClick={handleCancel} className="bg-manus-tertiary border-manus-border">
                                    返回列表
                                </Button>
                                <Button onClick={() => void handleSave()} disabled={busy} className="bg-accent text-white hover:bg-accent/90 shadow-sm shadow-accent/25">
                                    {editingId ? '更新题目' : '创建题目'}
                                </Button>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* ========== 删除确认弹窗 ========== */}
            <ConfirmDialog
                open={confirmOpen}
                onOpenChange={setConfirmOpen}
                title="确认删除此题目？"
                description={`题目 "${confirmTarget?.question_text?.slice(0, 30)}..." 将被永久删除，不可恢复。`}
                onConfirm={() => void handleDelete()}
                loading={busy}
                confirmText="删除"
                variant="destructive"
            />
        </div>
    );
}
