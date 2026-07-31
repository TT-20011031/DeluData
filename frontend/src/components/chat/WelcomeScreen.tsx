/**
 * 欢迎界面组件
 * 
 * 首次进入聊天页面时显示的欢迎界面
 * v2: 居中欢迎语样式（移除四个示例问题）
 */

export function WelcomeScreen() {
    return (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mx-auto max-w-4xl w-full">
            {/* Logo */}
            <div className="w-24 h-24 rounded-3xl bg-gradient-to-br from-accent/30 to-accent/10 flex items-center justify-center mb-8 shadow-xl">
                <span className="text-5xl text-accent font-bold">D</span>
            </div>

            {/* 欢迎语 */}
            <h1 className="text-4xl font-light text-manus-text mb-4 tracking-tight">
                有什么可以帮您？
            </h1>
            <p className="text-manus-muted text-base mb-4">
                用自然语言探索您的数据，或选择下方快捷工具开始
            </p>
        </div>
    )
}
