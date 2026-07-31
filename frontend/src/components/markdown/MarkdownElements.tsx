
// 标题组件
export const H1 = ({ children }: any) => <h1 className="text-2xl font-semibold mt-8 mb-4 text-manus-text tracking-tight border-b border-white/10 pb-2">{children}</h1>
export const H2 = ({ children }: any) => <h2 className="text-xl font-semibold mt-8 mb-4 text-manus-text tracking-tight">{children}</h2>
export const H3 = ({ children }: any) => <h3 className="text-lg font-medium mt-6 mb-3 text-manus-text">{children}</h3>

// 文本与列表组件
export const P = ({ children }: any) => <p className="leading-7 text-manus-text/90 my-4 text-[15px]">{children}</p>
export const Ul = ({ children }: any) => <ul className="my-4 ml-6 list-disc marker:text-accent/70 space-y-1 text-manus-text/90">{children}</ul>
export const Ol = ({ children }: any) => <ol className="my-4 ml-6 list-decimal marker:text-accent/70 space-y-1 text-manus-text/90">{children}</ol>
export const Li = ({ children }: any) => <li className="leading-7 pl-1">{children}</li>
export const Blockquote = ({ children }: any) => <blockquote className="border-l-4 border-accent/40 bg-white/5 pl-4 py-1 my-4 text-manus-muted italic rounded-r">{children}</blockquote>
export const A = ({ children, href }: any) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:text-accent/80 hover:underline underline-offset-4 decoration-accent/30 transition-colors bg-accent/5 px-1 rounded-sm">
        {children}
    </a>
)

// 分割线与表格
export const Hr = () => <hr className="my-8 border-t border-manus-border dark:border-white/20" />

export const Table = ({ children }: any) => (
    <div className="my-6 w-full overflow-x-auto rounded-lg border border-manus-border shadow-sm">
        <table className="w-full text-left text-sm border-collapse bg-manus-secondary">
            {children}
        </table>
    </div>
)
export const Thead = ({ children }: any) => <thead className="bg-manus-tertiary border-b border-manus-border">{children}</thead>
export const Th = ({ children }: any) => <th className="p-3 font-semibold border-b border-manus-border whitespace-nowrap text-manus-text">{children}</th>
export const Td = ({ children }: any) => <td className="p-3 border-b border-manus-border/50 text-manus-text/80 min-w-[100px] align-top leading-6">{children}</td>
