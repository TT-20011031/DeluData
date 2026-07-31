"""
博物馆模块 - 句子缓冲器

用于 TTS 句级流式处理：
将 LLM 流式输出按标点符号切割成完整句子，实现"边生成边朗读"
"""
from typing import List, Optional


class SentenceBuffer:
    """
    句子缓冲器（优化版）
    
    策略：
    1. 按中文标点符号切割，确保句子自然完整
    2. 短句（如"哈哈！"）合并到后续内容，避免语音碎片化
    3. 长句在逗号处适当拆分，避免单句过长
    4. 情感标签 [xxx] 不计入有效长度
    
    使用示例:
        buffer = SentenceBuffer()
        for chunk in llm_stream:
            sentences = buffer.feed(chunk)
            for sentence in sentences:
                await tts.synthesize(sentence)
        
        # 刷新剩余内容
        if remaining := buffer.flush():
            await tts.synthesize(remaining)
    """
    
    # 强句子结束标点（必须切分）
    STRONG_ENDINGS = {'。', '？', '\n'}
    
    # 弱句子结束标点（长度足够时切分）
    WEAK_ENDINGS = {'！', '；', '…'}
    
    # 逗号（超长句子时可切分）
    COMMA = {'，', ','}
    
    # 最小句子长度（不含情感标签）- 提高以减少 TTS 请求次数
    MIN_SENTENCE_LENGTH = 15
    
    # 理想句子长度 - 用长句换取流畅度
    IDEAL_SENTENCE_LENGTH = 30
    
    # 最大句子长度（超过时在逗号处切分）
    MAX_SENTENCE_LENGTH = 60
    
    def __init__(self):
        self.buffer = ""
    
    def _effective_length(self, text: str) -> int:
        """计算有效长度（不含情感标签）"""
        import re
        clean = re.sub(r'\[[a-zA-Z_]+\]', '', text)
        return len(clean.strip())
    
    def feed(self, chunk: str) -> List[str]:
        """
        喂入文本块，返回完整句子列表
        
        Args:
            chunk: 文本块 (来自 LLM 流式输出)
            
        Returns:
            完整句子列表 (可能为空)
        """
        self.buffer += chunk
        sentences = []
        
        while True:
            best_idx = -1
            best_priority = 0  # 1=逗号, 2=弱结束, 3=强结束
            
            for i, char in enumerate(self.buffer):
                candidate = self.buffer[:i + 1]
                eff_len = self._effective_length(candidate)
                
                # 强结束符：长度足够就切
                if char in self.STRONG_ENDINGS:
                    if eff_len >= self.MIN_SENTENCE_LENGTH:
                        best_idx = i
                        best_priority = 3
                        break
                    # 长度不够，继续找
                
                # 弱结束符：长度达到理想值才切
                elif char in self.WEAK_ENDINGS:
                    if eff_len >= self.IDEAL_SENTENCE_LENGTH:
                        best_idx = i
                        best_priority = 3
                        break
                    elif eff_len >= self.MIN_SENTENCE_LENGTH and best_priority < 2:
                        best_idx = i
                        best_priority = 2
                
                # 逗号：超长时切分
                elif char in self.COMMA:
                    if eff_len >= self.MAX_SENTENCE_LENGTH:
                        best_idx = i
                        best_priority = 3
                        break
                    elif eff_len >= self.IDEAL_SENTENCE_LENGTH and best_priority < 1:
                        best_idx = i
                        best_priority = 1
            
            # 只有高优先级或强结束才切分
            if best_idx == -1 or best_priority < 2:
                break
            
            # 切出完整句子
            sentence = self.buffer[:best_idx + 1].strip()
            self.buffer = self.buffer[best_idx + 1:].lstrip()
            
            if sentence:
                sentences.append(sentence)
        
        return sentences
    
    def flush(self) -> Optional[str]:
        """
        刷新剩余内容
        
        在流结束时调用，返回缓冲区中剩余的文本
        
        Returns:
            剩余文本，如果为空则返回 None
        """
        if self.buffer.strip():
            result = self.buffer.strip()
            self.buffer = ""
            return result
        return None
    
    def clear(self):
        """清空缓冲区"""
        self.buffer = ""
    
    @property
    def pending(self) -> str:
        """获取当前缓冲区内容 (调试用)"""
        return self.buffer
