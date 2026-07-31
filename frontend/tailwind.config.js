/** @type {import('tailwindcss').Config} */
export default {
    darkMode: 'class',
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            // Wabi-Sabi 诧寂风格配色
            colors: {
                // 主背景 - 深土褐色
                wabi: {
                    bg: {
                        DEFAULT: '#1a1816',      // 极深背景
                        secondary: '#252220',    // 次级背景 (侧边栏)
                        tertiary: '#2d2a27',     // 卡片/模态框背景
                        hover: '#353230',        // 悬浮状态
                    },
                    // 文本色
                    text: {
                        DEFAULT: '#c8c0b4',      // 主文本 (骨白/米白)
                        muted: '#8a857c',        // 次要文本
                        subtle: '#5c5850',       // 更淡的文本
                    },
                    // 边框
                    border: {
                        DEFAULT: '#3a3632',      // 主边框
                        strong: '#4a4640',       // 强调边框
                    },
                    // 强调色
                    accent: {
                        moss: '#6b7a6a',         // 苔藓绿
                        clay: '#9c6c5e',         // 陶土色
                        indigo: '#5b6e7c',       // 褪色靛蓝
                        gold: '#a89068',         // 哑光金
                    }
                }
            },
            fontFamily: {
                sans: ['Inter', 'Noto Sans SC', 'system-ui', 'sans-serif'],
                mono: ['JetBrains Mono', 'Consolas', 'monospace'],
            },
            borderRadius: {
                'wabi': '0.5rem',  // 温和的圆角
            },
            boxShadow: {
                'wabi': '0 4px 20px rgba(0, 0, 0, 0.15)',
                'wabi-lg': '0 8px 30px rgba(0, 0, 0, 0.25)',
            },
            animation: {
                'fade-in': 'fadeIn 0.3s ease-out',
                'slide-up': 'slideUp 0.3s ease-out',
                'pulse-subtle': 'pulseSubtle 2s ease-in-out infinite',
            },
            keyframes: {
                fadeIn: {
                    '0%': { opacity: '0' },
                    '100%': { opacity: '1' },
                },
                slideUp: {
                    '0%': { opacity: '0', transform: 'translateY(8px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
                pulseSubtle: {
                    '0%, 100%': { opacity: '1' },
                    '50%': { opacity: '0.7' },
                },
            },
        },
    },
    plugins: [],
}
