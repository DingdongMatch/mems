const LABEL_ZH = {
  expand: '放大',
  close: '关闭',
  dialog: 'Mermaid 图放大预览',
}

const LABEL_EN = {
  expand: 'Expand',
  close: 'Close',
  dialog: 'Mermaid diagram preview',
}

function labels() {
  const lang = (document.documentElement.lang || '').toLowerCase()
  return lang.startsWith('zh') ? LABEL_ZH : LABEL_EN
}

export function setupMermaidZoom() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return

  let modal: HTMLElement | null = null

  const ensureModal = () => {
    if (modal) return modal
    modal = document.createElement('div')
    modal.className = 'mermaid-zoom-modal'
    modal.innerHTML = `
      <div class="mermaid-zoom-box" role="dialog" aria-modal="true">
        <header class="mermaid-zoom-header">
          <span class="mermaid-zoom-title"></span>
          <button type="button" class="mermaid-zoom-close" aria-label="close"></button>
        </header>
        <div class="mermaid-zoom-stage"></div>
      </div>
    `
    document.body.appendChild(modal)

    const stage = modal.querySelector<HTMLElement>('.mermaid-zoom-stage')
    const close = () => {
      modal?.classList.remove('open')
      document.body.classList.remove('mermaid-zoom-open')
      if (stage) stage.replaceChildren()
    }

    modal.querySelector('.mermaid-zoom-close')?.addEventListener('click', close)
    modal.addEventListener('click', (event) => {
      if (event.target === modal) close()
    })
    return modal
  }

  const openModal = (source: HTMLElement) => {
    const sourceNode =
      source.tagName === 'IMG' ? source : source.querySelector('svg')
    if (!sourceNode) return

    const overlay = ensureModal()
    const stage = overlay.querySelector<HTMLElement>('.mermaid-zoom-stage')
    const title = overlay.querySelector<HTMLElement>('.mermaid-zoom-title')
    const closeBtn = overlay.querySelector<HTMLButtonElement>('.mermaid-zoom-close')
    const text = labels()

    if (!stage || !title || !closeBtn) return
    title.textContent = text.dialog
    closeBtn.textContent = `✕ ${text.close}`
    stage.replaceChildren(sourceNode.cloneNode(true))
    overlay.classList.add('open')
    document.body.classList.add('mermaid-zoom-open')
  }

  const createButton = (handler: () => void) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'mermaid-zoom-btn'
    const text = labels()
    button.textContent = `⤢ ${text.expand}`
    button.setAttribute('aria-label', text.expand)
    button.addEventListener('click', (event) => {
      event.stopPropagation()
      handler()
    })
    return button
  }

  const enhanceMermaid = (container: HTMLElement) => {
    if (container.dataset.mermaidZoomReady || !container.querySelector('svg')) return
    container.dataset.mermaidZoomReady = '1'
    container.classList.add('mermaid-zoom-wrap')

    container.appendChild(createButton(() => openModal(container)))
  }

  const enhanceImage = (image: HTMLImageElement) => {
    if (image.dataset.mermaidZoomReady) return
    image.dataset.mermaidZoomReady = '1'

    const wrapper = document.createElement('span')
    wrapper.className = 'mermaid-zoom-wrap image-zoom-wrap'
    image.replaceWith(wrapper)
    wrapper.appendChild(image)
    wrapper.appendChild(createButton(() => openModal(image)))
  }

  const scan = () => {
    document.querySelectorAll<HTMLElement>('.mermaid').forEach(enhanceMermaid)
    document
      .querySelectorAll<HTMLImageElement>('.vp-doc img[src$=".svg"]')
      .forEach(enhanceImage)
  }

  const onKeydown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      modal?.classList.remove('open')
      document.body.classList.remove('mermaid-zoom-open')
      const stage = modal?.querySelector('.mermaid-zoom-stage')
      if (stage) stage.replaceChildren()
    }
  }

  const boot = () => {
    const root = document.body ?? document.documentElement
    new MutationObserver(scan).observe(root, { childList: true, subtree: true })
    window.addEventListener('keydown', onKeydown)
    scan()
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', boot, { once: true })
  } else {
    boot()
  }
}
