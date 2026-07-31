/**
 * FlickeringGrid 动态背景组件
 * 
 * Magic UI 风格的闪烁网格效果
 */
import { useEffect, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'

interface FlickeringGridProps {
    squareSize?: number
    gridGap?: number
    flickerChance?: number
    color?: string
    maxOpacity?: number
    className?: string
}

export default function FlickeringGrid({
    squareSize = 4,
    gridGap = 6,
    flickerChance = 0.3,
    color = 'rgb(0, 136, 255)',
    maxOpacity = 0.3,
    className,
}: FlickeringGridProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const containerRef = useRef<HTMLDivElement>(null)

    const setupCanvas = useCallback(() => {
        const canvas = canvasRef.current
        const container = containerRef.current
        if (!canvas || !container) return

        const dpr = window.devicePixelRatio || 1
        const rect = container.getBoundingClientRect()

        canvas.width = rect.width * dpr
        canvas.height = rect.height * dpr
        canvas.style.width = `${rect.width}px`
        canvas.style.height = `${rect.height}px`

        const ctx = canvas.getContext('2d')
        if (!ctx) return

        ctx.scale(dpr, dpr)

        return { ctx, width: rect.width, height: rect.height }
    }, [])

    useEffect(() => {
        const result = setupCanvas()
        if (!result) return

        const { ctx, width, height } = result

        const cols = Math.ceil(width / (squareSize + gridGap))
        const rows = Math.ceil(height / (squareSize + gridGap))

        // 初始化格子透明度
        const grid: number[][] = []
        for (let i = 0; i < rows; i++) {
            grid[i] = []
            for (let j = 0; j < cols; j++) {
                grid[i][j] = Math.random() * maxOpacity
            }
        }

        const draw = () => {
            ctx.clearRect(0, 0, width, height)

            for (let i = 0; i < rows; i++) {
                for (let j = 0; j < cols; j++) {
                    // 随机闪烁
                    if (Math.random() < flickerChance) {
                        grid[i][j] = Math.random() * maxOpacity
                    }

                    const x = j * (squareSize + gridGap)
                    const y = i * (squareSize + gridGap)

                    ctx.fillStyle = color.replace('rgb', 'rgba').replace(')', `, ${grid[i][j]})`)
                    ctx.fillRect(x, y, squareSize, squareSize)
                }
            }
        }

        let animationId: number
        const animate = () => {
            draw()
            animationId = requestAnimationFrame(animate)
        }

        // 降低帧率以减少 CPU 使用
        const intervalId = setInterval(() => {
            draw()
        }, 100)

        const handleResize = () => {
            setupCanvas()
        }

        window.addEventListener('resize', handleResize)

        return () => {
            clearInterval(intervalId)
            if (animationId) cancelAnimationFrame(animationId)
            window.removeEventListener('resize', handleResize)
        }
    }, [setupCanvas, squareSize, gridGap, flickerChance, color, maxOpacity])

    return (
        <div
            ref={containerRef}
            className={cn("absolute inset-0 overflow-hidden", className)}
        >
            <canvas
                ref={canvasRef}
                className="w-full h-full"
            />
        </div>
    )
}
