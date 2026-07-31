/** @type {import('tailwindcss').Config} */
export default {
    darkMode: 'class',
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                playground: {
                    red: '#FF6138',
                    green: '#00A388',
                    yellow: '#FFCE00',
                    blue: '#3498DB',
                    bg: '#F9F9F9',
                }
            },
            fontFamily: {
                sans: ['ZCOOL KuaiLe', 'Noto Sans SC', 'system-ui', 'sans-serif'],
            },
            boxShadow: {
                'cartoon': '4px 4px 0px #000',
                'cartoon-hover': '2px 2px 0px #000',
            },
            animation: {
                'bounce-sm': 'bounceSm 1s infinite alternate',
                'shake': 'shake 0.5s',
            },
            keyframes: {
                bounceSm: {
                    '0%': { transform: 'translateY(0)' },
                    '100%': { transform: 'translateY(-10px)' },
                },
                shake: {
                    '0%, 100%': { transform: 'translateX(0)' },
                    '10%, 30%, 50%, 70%, 90%': { transform: 'translateX(-5px)' },
                    '20%, 40%, 60%, 80%': { transform: 'translateX(5px)' },
                }
            }
        },
    },
    plugins: [],
}
