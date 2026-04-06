import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Mems',
  description: 'A layered, self-distilling, century-scale memory system for AI agents.',
  base: '/mems/',
  cleanUrls: true,
  lastUpdated: true,
  themeConfig: {
    socialLinks: [{ icon: 'github', link: 'https://github.com/DingdongMatch/mems' }],
    search: { provider: 'local' }
  },
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      link: '/',
      themeConfig: {
        nav: [
          { text: 'Home', link: '/' },
          { text: 'Quick Start', link: '/quick-start' },
          { text: 'Integration', link: '/integration/overview' },
          { text: 'Architecture', link: '/introduction/architecture' },
          { text: 'GitHub', link: 'https://github.com/DingdongMatch/mems' }
        ],
        sidebar: {
          '/': [
            {
              text: 'Introduction',
              items: [
                { text: 'What Is Mems', link: '/introduction/what-is-mems' },
                { text: 'Design Philosophy', link: '/introduction/design-philosophy' },
                { text: 'Architecture Overview', link: '/introduction/architecture' }
              ]
            },
            {
              text: 'Getting Started',
              items: [{ text: 'Quick Start', link: '/quick-start' }]
            },
            {
              text: 'Integration',
              items: [
                { text: 'Overview', link: '/integration/overview' },
                { text: 'Identity Model', link: '/integration/identity-model' }
              ]
            },
            {
              text: 'API Guides',
              items: [
                { text: 'Context', link: '/api/context' },
                { text: 'Query', link: '/api/query' },
                { text: 'Write', link: '/api/write' },
                { text: 'Status', link: '/api/status' },
                { text: 'Health', link: '/api/health' }
              ]
            },
            {
              text: 'Quality',
              items: [{ text: 'Benchmark and Quality', link: '/quality/benchmarks' }]
            },
            {
              text: 'Development',
              items: [{ text: 'Contributing', link: '/development/contributing' }]
            }
          ]
        }
      }
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      themeConfig: {
        nav: [
          { text: '首页', link: '/zh/' },
          { text: '快速开始', link: '/zh/quick-start' },
          { text: '接入指南', link: '/zh/integration/overview' },
          { text: '架构', link: '/zh/introduction/architecture' },
          { text: 'GitHub', link: 'https://github.com/DingdongMatch/mems' }
        ],
        sidebar: {
          '/zh/': [
            {
              text: '项目介绍',
              items: [
                { text: '什么是 Mems', link: '/zh/introduction/what-is-mems' },
                { text: '设计哲学', link: '/zh/introduction/design-philosophy' },
                { text: '架构总览', link: '/zh/introduction/architecture' }
              ]
            },
            {
              text: '快速开始',
              items: [{ text: '本地运行', link: '/zh/quick-start' }]
            },
            {
              text: '接入指南',
              items: [
                { text: '接入总览', link: '/zh/integration/overview' },
                { text: 'Identity Model', link: '/zh/integration/identity-model' }
              ]
            },
            {
              text: 'API 指南',
              items: [
                { text: 'Context', link: '/zh/api/context' },
                { text: 'Query', link: '/zh/api/query' },
                { text: 'Write', link: '/zh/api/write' },
                { text: 'Status', link: '/zh/api/status' },
                { text: 'Health', link: '/zh/api/health' }
              ]
            },
            {
              text: '质量',
              items: [{ text: 'Benchmark 与质量', link: '/zh/quality/benchmarks' }]
            },
            {
              text: '开发',
              items: [{ text: '贡献指南', link: '/zh/development/contributing' }]
            }
          ]
        }
      }
    }
  }
})
