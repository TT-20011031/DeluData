/**
 * AI 生成 Skill 弹窗
 * 当前前端入口已暂停使用，组件保留用于后续恢复。
 */
import { useState } from 'react'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Loader2, Sparkles } from 'lucide-react'

interface GenerateDialogProps {
    open: boolean
    onClose: () => void
    onGenerate: (userInput: string) => Promise<void>
    isGenerating: boolean
}

export function SkillGenerateDialog({
    open,
    onClose,
    onGenerate,
    isGenerating,
}: GenerateDialogProps) {
    const [userInput, setUserInput] = useState('')

    const handleGenerate = async () => {
        if (!userInput.trim()) return
        await onGenerate(userInput)
        setUserInput('')
    }

    const handleClose = () => {
        setUserInput('')
        onClose()
    }

    return (
        <Dialog open={open} onOpenChange={(open) => !open && handleClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-lg">
                <DialogHeader>
                    <DialogTitle className="text-manus-text flex items-center gap-2">
                        <Sparkles className="h-5 w-5 text-accent" />
                        AI 生成 DeluSkills
                    </DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <Label className="text-manus-text">描述你的需求</Label>
                        <Textarea
                            value={userInput}
                            onChange={(e) => setUserInput(e.target.value)}
                            placeholder="例：我想生成一个查询各部门销售额排名并生成图表的操作流程..."
                            rows={5}
                            className="bg-manus border-manus-border text-manus-text resize-none"
                            disabled={isGenerating}
                        />
                        <p className="text-xs text-manus-muted">
                            AI 将根据你的描述自动生成 DeluSkills 结构，你可以在之后进行编辑调整
                        </p>
                    </div>
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={handleClose}
                        disabled={isGenerating}
                        className="bg-manus-tertiary border-manus-border text-manus-text"
                    >
                        取消
                    </Button>
                    <Button
                        onClick={handleGenerate}
                        disabled={isGenerating || !userInput.trim()}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        {isGenerating ? (
                            <>
                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                生成中...
                            </>
                        ) : (
                            <>
                                <Sparkles className="h-4 w-4 mr-2" />
                                开始生成
                            </>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
