/**
 * 唤醒词与音色 Tab
 *
 * 功能：唤醒词配置、TTS 音色配置
 * 改造项：中文 Label、Select 替代自由输入、Switch、Toast
 */
import { useEffect, useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/components/ui/toast';
import { scienceAdminService } from '@/services/scienceAdminService';
import type { ScienceWakewordConfig, ScienceVoiceProfile } from '@/types/scienceAdmin';

/** 可选音色列表（来源：火山引擎 TTS 音色库） */
const VOICE_OPTIONS = [
    { value: 'child', label: '童声' },
    { value: 'robot', label: '机器人' },
    { value: 'female_gentle', label: '女声 · 温柔' },
    { value: 'male_broadcast', label: '男声 · 播报' },
    { value: 'default', label: '默认' },
];

export default function VoiceTab() {
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    const [wakeword, setWakeword] = useState<ScienceWakewordConfig>({ wakeword: '', enabled: true, sensitivity: 0.65 });
    const [voiceProfile, setVoiceProfile] = useState<ScienceVoiceProfile>({
        child_voice: 'child',
        adult_voice: 'female_gentle',
        elder_voice: 'male_broadcast',
        female_voice: 'female_gentle',
        male_voice: 'male_broadcast',
        default_voice: 'robot',
        gender_confidence_threshold: 0.75,
    });

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [w, v] = await Promise.all([
                scienceAdminService.getWakeword(),
                scienceAdminService.getVoiceProfile(),
            ]);
            setWakeword(w);
            setVoiceProfile(v);
        } catch (err) {
            toast({ type: 'error', title: '加载配置失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { void loadData(); }, [loadData]);

    const saveWakeword = async () => {
        if (!wakeword.wakeword.trim()) {
            toast({ type: 'warning', title: '请填写唤醒词' });
            return;
        }
        setBusy(true);
        try {
            setWakeword(await scienceAdminService.updateWakeword(wakeword));
            toast({ type: 'success', title: '唤醒词配置已保存' });
        } catch (err) {
            toast({ type: 'error', title: '保存失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setBusy(false);
        }
    };

    const saveVoice = async () => {
        setBusy(true);
        try {
            setVoiceProfile(await scienceAdminService.updateVoiceProfile(voiceProfile));
            toast({ type: 'success', title: '音色配置已保存' });
        } catch (err) {
            toast({ type: 'error', title: '保存失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setBusy(false);
        }
    };

    if (loading) {
        return <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10"><CardContent className="py-10 text-center text-manus-muted">加载配置中...</CardContent></Card>;
    }

    return (
        <div className="space-y-6">
            {/* 唤醒词配置 */}
            <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
                <CardHeader className="border-b border-manus-border/50"><CardTitle>唤醒词配置</CardTitle></CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label>唤醒词</Label>
                            <Input
                                placeholder="如：小莎小莎"
                                value={wakeword.wakeword}
                                onChange={(e) => setWakeword((s) => ({ ...s, wakeword: e.target.value }))}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>灵敏度 <span className="text-manus-muted text-xs">(0.1 ~ 1.0，越高越容易触发)</span></Label>
                            <Input
                                type="number"
                                step="0.05"
                                min={0.1}
                                max={1}
                                value={wakeword.sensitivity}
                                onChange={(e) => setWakeword((s) => ({ ...s, sensitivity: Number(e.target.value) }))}
                            />
                        </div>
                    </div>
                    <div className="flex items-center gap-4">
                        <div className="flex items-center gap-2">
                            <Label>启用唤醒词</Label>
                            <Switch checked={wakeword.enabled} onCheckedChange={(v) => setWakeword((s) => ({ ...s, enabled: v }))} />
                            <span className="text-sm text-manus-muted">{wakeword.enabled ? '已开启' : '已关闭'}</span>
                        </div>
                        <div className="flex-1" />
                        <Button onClick={() => void saveWakeword()} disabled={busy} className="bg-accent text-white hover:bg-accent/90 shadow-sm shadow-accent/25">保存唤醒词配置</Button>
                    </div>
                </CardContent>
            </Card>

            {/* TTS 音色配置 */}
            <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
                <CardHeader className="border-b border-manus-border/50"><CardTitle>TTS 音色配置</CardTitle></CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label>儿童音色</Label>
                            <Select value={voiceProfile.child_voice} onValueChange={(v) => setVoiceProfile((s) => ({ ...s, child_voice: v }))}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {VOICE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>成人音色</Label>
                            <Select
                                value={voiceProfile.adult_voice}
                                onValueChange={(v) => setVoiceProfile((s) => ({ ...s, adult_voice: v, female_voice: v }))}
                            >
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {VOICE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>长者音色</Label>
                            <Select
                                value={voiceProfile.elder_voice}
                                onValueChange={(v) => setVoiceProfile((s) => ({ ...s, elder_voice: v, male_voice: v }))}
                            >
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {VOICE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>默认音色 <span className="text-manus-muted text-xs">(年龄未知时使用)</span></Label>
                            <Select value={voiceProfile.default_voice} onValueChange={(v) => setVoiceProfile((s) => ({ ...s, default_voice: v }))}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {VOICE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>兼容回退阈值 <span className="text-manus-muted text-xs">(旧 gender 链路低于此值时使用默认音色)</span></Label>
                            <Input
                                type="number"
                                step="0.05"
                                min={0.5}
                                max={0.99}
                                value={voiceProfile.gender_confidence_threshold}
                                onChange={(e) => setVoiceProfile((s) => ({ ...s, gender_confidence_threshold: Number(e.target.value) }))}
                            />
                        </div>
                    </div>
                    <div className="flex justify-end">
                        <Button onClick={() => void saveVoice()} disabled={busy} className="bg-accent text-white hover:bg-accent/90 shadow-sm shadow-accent/25">保存音色配置</Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
