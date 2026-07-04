/**
 * Tailwind CSS v4 配置。
 *
 * v4 的主题（颜色、字体、圆角、阴影等）主要通过 src/index.css 中的
 * `@theme inline { ... }` 与 `:root` CSS 变量声明，沿用 artoo 的配色与排版。
 * 本文件保留用于 content 扫描范围声明与 IDE / 工具链兼容。
 */
export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  darkMode: ['class', '&:is(.dark *)'],
  theme: {
    extend: {},
  },
  plugins: [],
}
