(() => {
  if (window.__xianyuAiQueueOverlay) return
  window.__xianyuAiQueueOverlay = true

  const BOT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/></svg>'
  const CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>'
  const CHEVRON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>'

  const state = {
    open: false,
    snapshot: { concurrency: 1, active_count: 0, waiting_count: 0, items: [] },
    generationJobs: [],
    pipelineOpen: false,
    pipelineJobId: '',
    pipelineJob: null,
    pipelineError: '',
  }

  function locale() {
    return (document.documentElement.lang || localStorage.getItem('app_locale') || 'zh-CN')
      .toLowerCase()
      .startsWith('zh') ? 'zh' : 'en'
  }

  function messages() {
    return locale() === 'zh' ? {
      button: 'AI 分析队列',
      open: '打开 AI 分析队列',
      title: 'AI 分析队列',
      summary: (running, waiting, concurrency) => `运行 ${running} · 等待 ${waiting} · 全局并发 ${concurrency}`,
      criteria: 'Criteria Prompt 生成',
      analysis: '商品判断',
      other: '其他 LLM 请求',
      streaming: '流式输出中',
      waiting: '排队中',
      retrying: '重试中',
      pipelineRunning: '任务流水线',
      retryBody: (attempt, maximum, error) => `正在重试中 ${attempt}/${maximum}\n错误原因：${error}`,
      awaiting: '已连接模型，等待正文输出…',
      eta: minutes => `预计还有 ${minutes} 分钟`,
      empty: '当前没有 LLM 请求',
      emptyHint: '新的商品判断或 Criteria 生成请求会显示在这里。',
      unknown: '未命名任务',
      close: '关闭',
      pipelineOpen: '查看任务生成流水线',
      pipelineTitle: 'AI 标准生成进度',
      pipelineDescription: 'AI 正在生成或更新分析标准。',
      pipelineLoading: '正在读取任务生成进度…',
      pipelineFailed: '读取任务生成进度失败',
      pipelineReceiving: '正在接收流式输出',
      generatedCharacters: count => `已生成 ${count} 字符`,
      pipelineHelperFailed: '生成失败后会保留这个弹窗，方便你查看错误原因。',
      pipelineHelperRunning: '任务已转入后台生成，你可以停留在这里查看步骤，也可以先关闭弹窗。',
      pipelineCloseWindow: '关闭窗口',
      pipelineStatus: { completed: '已完成', failed: '失败', running: '生成中', queued: '排队中' },
    } : {
      button: 'AI Queue',
      open: 'Open AI analysis queue',
      title: 'AI Analysis Queue',
      summary: (running, waiting, concurrency) => `${running} running · ${waiting} waiting · concurrency ${concurrency}`,
      criteria: 'Criteria Prompt',
      analysis: 'Item Analysis',
      other: 'Other LLM Request',
      streaming: 'Streaming',
      waiting: 'Waiting',
      retrying: 'Retrying',
      pipelineRunning: 'Pipeline',
      retryBody: (attempt, maximum, error) => `Retrying ${attempt}/${maximum}\nError: ${error}`,
      awaiting: 'Connected; waiting for output…',
      eta: minutes => `Estimated wait: ${minutes} min`,
      empty: 'No LLM requests',
      emptyHint: 'New item analyses and Criteria requests will appear here.',
      unknown: 'Unnamed task',
      close: 'Close',
      pipelineOpen: 'View generation pipeline',
      pipelineTitle: 'AI Criteria Generation',
      pipelineDescription: 'AI is generating or updating analysis criteria.',
      pipelineLoading: 'Loading generation progress…',
      pipelineFailed: 'Failed to load generation progress',
      pipelineReceiving: 'Receiving streamed output',
      generatedCharacters: count => `${count} characters generated`,
      pipelineHelperFailed: 'The dialog stays open after a failure so you can inspect the error.',
      pipelineHelperRunning: 'The task is generating in the background. You can watch progress here or close this dialog.',
      pipelineCloseWindow: 'Close Window',
      pipelineStatus: { completed: 'Completed', failed: 'Failed', running: 'Running', queued: 'Queued' },
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  const overlay = element('div')
  overlay.id = 'ai-queue-overlay'
  overlay.dataset.open = 'false'

  const backdrop = element('button')
  backdrop.id = 'ai-queue-backdrop'
  backdrop.type = 'button'

  const drawer = element('aside')
  drawer.id = 'ai-queue-drawer'
  drawer.setAttribute('role', 'dialog')
  drawer.setAttribute('aria-modal', 'true')

  const drawerHeader = element('header', 'ai-queue-drawer-header')
  const headerRow = element('div', 'ai-queue-header-row')
  const heading = element('div', 'ai-queue-heading')
  const headingIcon = element('span', 'ai-queue-heading-icon')
  headingIcon.innerHTML = BOT_ICON
  const headingText = element('div')
  const title = element('h2', 'ai-queue-title')
  const summary = element('p', 'ai-queue-summary')
  headingText.append(title, summary)
  heading.append(headingIcon, headingText)
  const closeButton = element('button', 'ai-queue-close')
  closeButton.type = 'button'
  closeButton.innerHTML = CLOSE_ICON
  headerRow.append(heading, closeButton)
  const recoverySlot = element('div')
  recoverySlot.id = 'ai-recovery-action-slot'
  drawerHeader.append(headerRow, recoverySlot)

  const list = element('div', 'ai-queue-list')
  drawer.append(drawerHeader, list)
  overlay.append(backdrop, drawer)
  document.body.append(overlay)

  const pipelineOverlay = element('div')
  pipelineOverlay.id = 'ai-generation-pipeline-overlay'
  pipelineOverlay.dataset.open = 'false'
  const pipelineBackdrop = element('button')
  pipelineBackdrop.id = 'ai-generation-pipeline-backdrop'
  pipelineBackdrop.type = 'button'
  const pipelineDialog = element('section')
  pipelineDialog.id = 'ai-generation-pipeline-dialog'
  pipelineDialog.setAttribute('role', 'dialog')
  pipelineDialog.setAttribute('aria-modal', 'true')
  const pipelineContent = element('div', 'ai-generation-pipeline-content')
  pipelineDialog.append(pipelineContent)
  pipelineOverlay.append(pipelineBackdrop, pipelineDialog)
  document.body.append(pipelineOverlay)

  const pill = element('button')
  pill.id = 'ai-queue-pill'
  pill.type = 'button'
  pill.dataset.running = 'false'
  const pillIcon = element('span', 'ai-queue-pill-icon')
  pillIcon.innerHTML = BOT_ICON
  pillIcon.append(element('span', 'ai-queue-live-dot'))
  const pillLabel = element('span', 'ai-queue-pill-label')
  const pillCount = element('span', 'ai-queue-pill-count', '0')
  pill.append(pillIcon, pillLabel, pillCount)

  function updateStaticText() {
    const text = messages()
    pillLabel.textContent = text.button
    pill.setAttribute('aria-label', text.open)
    pill.title = text.open
    title.textContent = text.title
    drawer.setAttribute('aria-label', text.title)
    backdrop.setAttribute('aria-label', text.close)
    closeButton.setAttribute('aria-label', text.close)
  }

  function mountPill() {
    if (pill.isConnected) return
    const header = document.querySelector('header')
    if (!header) return
    const localeGroup = Array.from(header.querySelectorAll('[role="group"]')).find(node => {
      const content = node.textContent || ''
      return content.includes('English') || content.includes('中文')
    })
    if (!localeGroup) return
    const localeWrapper = localeGroup.parentElement || localeGroup
    localeWrapper.parentElement?.insertBefore(pill, localeWrapper)
  }

  function findRecoveryButton() {
    return Array.from(document.querySelectorAll('button')).find(button => (
      button !== pill
      && (
        button.classList.contains('ai-analysis-control')
        || button.getAttribute('aria-label') === '启动 AI 补分析'
        || button.getAttribute('aria-label') === '停止当前 AI 分析与重试'
      )
    ))
  }

  function moveRecoveryButton() {
    const button = findRecoveryButton()
    if (!button) return
    button.classList.add('ai-queue-recovery-action')
    if (button.parentElement !== recoverySlot) recoverySlot.append(button)
  }

  function kind(item) {
    if (item.kind === 'criteria' || item.generation_job_id) return 'criteria'
    if (item.kind === 'analysis') return 'analysis'
    return 'other'
  }

  function pipelineStatus(job) {
    if (!job) return 'queued'
    return ['completed', 'failed', 'running'].includes(job.status) ? job.status : 'queued'
  }

  function renderPipeline() {
    const text = messages()
    pipelineContent.replaceChildren()

    const header = element('header', 'ai-generation-pipeline-header')
    const headerText = element('div')
    headerText.append(
      element('h2', 'ai-generation-pipeline-title', text.pipelineTitle),
      element('p', 'ai-generation-pipeline-description', text.pipelineDescription),
    )
    const headerClose = element('button', 'ai-generation-pipeline-x')
    headerClose.type = 'button'
    headerClose.innerHTML = CLOSE_ICON
    headerClose.setAttribute('aria-label', text.close)
    headerClose.addEventListener('click', closePipeline)
    header.append(headerText, headerClose)
    pipelineContent.append(header)

    if (!state.pipelineJob) {
      const loading = element(
        'div',
        state.pipelineError ? 'ai-generation-pipeline-load-error' : 'ai-generation-pipeline-loading',
        state.pipelineError || text.pipelineLoading,
      )
      pipelineContent.append(loading)
    } else {
      const job = state.pipelineJob
      const status = pipelineStatus(job)
      const panel = element('section', 'ai-generation-pipeline-panel')
      const lead = element('div', 'ai-generation-pipeline-lead')
      const leadText = element('div')
      leadText.append(
        element('h3', 'ai-generation-pipeline-task', job.task_name || text.unknown),
        element('p', 'ai-generation-pipeline-message', job.message || ''),
      )
      const badge = element('span', 'ai-generation-pipeline-badge', text.pipelineStatus[status])
      badge.dataset.status = status
      lead.append(leadText, badge)
      panel.append(lead)

      if (status === 'running' && job.current_step === 'llm') {
        const stream = element('div', 'ai-generation-pipeline-stream')
        const receiving = element('span')
        receiving.append(element('i'), document.createTextNode(text.pipelineReceiving))
        stream.append(
          receiving,
          element('strong', '', text.generatedCharacters(Number(job.generated_characters || 0))),
        )
        panel.append(stream)
      }

      const steps = element('div', 'ai-generation-pipeline-steps')
      ;(Array.isArray(job.steps) ? job.steps : []).forEach(step => {
        const row = element('div', 'ai-generation-pipeline-step')
        const dot = element('span', 'ai-generation-pipeline-step-dot')
        dot.dataset.status = step.status || 'pending'
        const copy = element('div')
        const label = element('p', 'ai-generation-pipeline-step-label', step.label || '')
        label.dataset.status = step.status || 'pending'
        copy.append(label)
        if (step.message) {
          const message = element('p', 'ai-generation-pipeline-step-message', step.message)
          message.dataset.status = step.status || 'pending'
          copy.append(message)
        }
        row.append(dot, copy)
        steps.append(row)
      })
      panel.append(steps)
      if (job.error) panel.append(element('p', 'ai-generation-pipeline-error', job.error))
      pipelineContent.append(panel)
      pipelineContent.append(element(
        'p',
        'ai-generation-pipeline-helper',
        status === 'failed' ? text.pipelineHelperFailed : text.pipelineHelperRunning,
      ))
    }

    const footer = element('footer', 'ai-generation-pipeline-footer')
    const activeStatus = pipelineStatus(state.pipelineJob)
    const close = element(
      'button',
      'ai-generation-pipeline-close',
      activeStatus === 'queued' || activeStatus === 'running' ? text.pipelineCloseWindow : text.close,
    )
    close.type = 'button'
    close.addEventListener('click', closePipeline)
    footer.append(close)
    pipelineContent.append(footer)
  }

  async function refreshPipeline() {
    if (!state.pipelineOpen || !state.pipelineJobId) return
    try {
      const response = await fetch(`/api/tasks/generate-jobs/${encodeURIComponent(state.pipelineJobId)}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) throw new Error(`${messages().pipelineFailed} (${response.status})`)
      const payload = await response.json()
      state.pipelineJob = payload.job || null
      state.pipelineError = ''
    } catch (error) {
      state.pipelineError = error instanceof Error ? error.message : messages().pipelineFailed
    }
    renderPipeline()
  }

  function openPipeline(jobId) {
    if (!jobId) return
    state.pipelineOpen = true
    state.pipelineJobId = jobId
    state.pipelineJob = null
    state.pipelineError = ''
    pipelineOverlay.dataset.open = 'true'
    renderPipeline()
    void refreshPipeline()
  }

  function closePipeline() {
    state.pipelineOpen = false
    state.pipelineJobId = ''
    pipelineOverlay.dataset.open = 'false'
    document.body.style.overflow = state.open ? 'hidden' : ''
  }

  function render() {
    const text = messages()
    const data = state.snapshot
    const queueItems = Array.isArray(data.items) ? data.items : []
    const representedJobs = new Set(
      queueItems.map(item => item.generation_job_id).filter(Boolean),
    )
    const pipelineItems = state.generationJobs
      .filter(job => !representedJobs.has(job.job_id))
      .map(job => ({
        ticket_id: `generation-job-${job.job_id}`,
        kind: 'criteria',
        status: job.status === 'queued' ? 'waiting' : 'running',
        summary: job.task_name,
        stream_content: job.message,
        estimated_wait_seconds: 0,
        generation_job_id: job.job_id,
        pipeline_only: true,
      }))
    const items = [...pipelineItems, ...queueItems]
    const running = items.filter(item => item.status === 'running' || item.status === 'retrying').length
    const waiting = items.filter(item => item.status !== 'running' && item.status !== 'retrying').length
    pillCount.textContent = String(running + waiting)
    pill.dataset.running = running > 0 ? 'true' : 'false'
    summary.textContent = text.summary(running, waiting, Number(data.concurrency || 1))
    list.replaceChildren()

    if (!items.length) {
      const empty = element('div', 'ai-queue-empty', text.empty)
      empty.append(element('small', '', text.emptyHint))
      list.append(empty)
      return
    }

    items.forEach(item => {
      const itemKind = kind(item)
      const linkedJob = item.generation_job_id
        ? state.generationJobs.find(job => job.job_id === item.generation_job_id)
        : null
      const status = item.status === 'running'
        ? 'running'
        : item.status === 'retrying' ? 'retrying' : 'waiting'
      const card = element('article', 'ai-queue-card')
      card.dataset.status = status
      const top = element('div', 'ai-queue-card-top')
      const indicator = element('span', 'ai-queue-state-indicator')
      indicator.dataset.status = status
      indicator.setAttribute('aria-hidden', 'true')
      if (status === 'waiting') {
        indicator.append(element('i'), element('i'), element('i'))
      }
      const main = element('div', 'ai-queue-card-main')
      const tag = element(
        'span',
        'ai-queue-kind',
        itemKind === 'criteria' ? text.criteria : itemKind === 'analysis' ? text.analysis : text.other,
      )
      tag.dataset.kind = itemKind
      const itemTitle = element('h3', 'ai-queue-item-title', item.summary || text.unknown)
      main.append(tag, itemTitle)
      const statusTag = element(
        'span',
        'ai-queue-status',
        status === 'running'
          ? item.pipeline_only || (linkedJob && linkedJob.current_step !== 'llm')
            ? text.pipelineRunning
            : text.streaming
          : status === 'retrying' ? text.retrying : text.waiting,
      )
      statusTag.dataset.status = status
      const actions = element('div', 'ai-queue-card-actions')
      actions.append(statusTag)
      if (itemKind === 'criteria' && item.generation_job_id) {
        const pipelineButton = element('button', 'ai-queue-pipeline-button')
        pipelineButton.type = 'button'
        pipelineButton.innerHTML = CHEVRON_ICON
        pipelineButton.setAttribute('aria-label', text.pipelineOpen)
        pipelineButton.title = text.pipelineOpen
        pipelineButton.addEventListener('click', () => openPipeline(item.generation_job_id))
        actions.append(pipelineButton)
      }
      top.append(indicator, main, actions)
      card.append(top)
      const retryAttempt = Number(item.retry_attempt || 0)
      const retryMaximum = Number(item.retry_max_attempts || 0)
      if (retryAttempt > 0 && retryMaximum > 0) {
        card.append(element(
          'p',
          'ai-queue-content ai-queue-retry-content',
          text.retryBody(retryAttempt, retryMaximum, item.retry_error || '—'),
        ))
      } else if (status === 'running') {
        card.append(element(
          'p',
          'ai-queue-content',
          item.stream_content || linkedJob?.message || text.awaiting,
        ))
      } else {
        const minutes = Math.max(1, Math.ceil(Number(item.estimated_wait_seconds || 0) / 60))
        card.append(element('p', 'ai-queue-eta', text.eta(minutes)))
      }
      list.append(card)
    })
  }

  function setOpen(open) {
    state.open = open
    overlay.dataset.open = open ? 'true' : 'false'
    document.body.style.overflow = open ? 'hidden' : ''
    if (open) void refresh()
  }

  async function refresh() {
    try {
      const requestOptions = {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      }
      const [queueResponse, jobsResponse] = await Promise.all([
        fetch('/api/settings/ai/queue', requestOptions),
        fetch('/api/tasks/generation/jobs?active_only=true', requestOptions),
      ])
      if (queueResponse.ok) state.snapshot = await queueResponse.json()
      if (jobsResponse.ok) {
        const payload = await jobsResponse.json()
        state.generationJobs = Array.isArray(payload.jobs) ? payload.jobs : []
      }
      render()
    } catch {
      // Keep header polling quiet while the backend is restarting.
    }
  }

  pill.addEventListener('click', () => setOpen(true))
  backdrop.addEventListener('click', () => setOpen(false))
  closeButton.addEventListener('click', () => setOpen(false))
  pipelineBackdrop.addEventListener('click', closePipeline)
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return
    if (state.pipelineOpen) closePipeline()
    else if (state.open) setOpen(false)
  })

  let mutationFrame = 0
  const observer = new MutationObserver(() => {
    if (mutationFrame) return
    mutationFrame = requestAnimationFrame(() => {
      mutationFrame = 0
      mountPill()
      moveRecoveryButton()
    })
  })
  observer.observe(document.body, { childList: true, subtree: true })

  updateStaticText()
  mountPill()
  moveRecoveryButton()
  render()
  window.setInterval(() => {
    updateStaticText()
    mountPill()
    moveRecoveryButton()
    void refresh()
    void refreshPipeline()
  }, 1000)
  void refresh()
})()
