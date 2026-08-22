document.addEventListener('alpine:init', () => {
  Alpine.data('app', () => ({
    // ---------------------------------------------------------------
    // State
    // ---------------------------------------------------------------
    dark: document.documentElement.classList.contains('dark'),

    documents: [],
    documentsLoading: true,
    documentFilter: '',
    uploads: [],
    dragActive: false,
    pollHandle: null,
    expandedDocumentId: null,
    chunksByDocument: {}, // documentId -> {loading, error, items: [...]}

    models: [],
    selectedModel: null,
    modelMenuOpen: false,

    sessions: [],
    currentSessionId: null,
    historyOpen: false,

    messages: [],
    messagesLoading: false,
    composerText: '',
    streaming: false,

    sourcesOpen: true,
    lastCitations: [],
    highlightedRank: null,

    toasts: [],
    toastSeq: 0,

    // ---------------------------------------------------------------
    // Lifecycle
    // ---------------------------------------------------------------
    async init() {
      await Promise.all([this.loadDocuments(), this.loadModels(), this.loadSessions()]);
    },

    // ---------------------------------------------------------------
    // Theme
    // ---------------------------------------------------------------
    toggleTheme() {
      this.dark = !this.dark;
      document.documentElement.classList.toggle('dark', this.dark);
      localStorage.setItem('siresoft-theme', this.dark ? 'dark' : 'light');
    },

    // ---------------------------------------------------------------
    // Keyboard shortcuts
    // ---------------------------------------------------------------
    handleKeydown(e) {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        document.getElementById('doc-search')?.focus();
      } else if (mod && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        this.newChat();
      }
    },

    // ---------------------------------------------------------------
    // Documents
    // ---------------------------------------------------------------
    get filteredDocuments() {
      const q = this.documentFilter.trim().toLowerCase();
      if (!q) return this.documents;
      return this.documents.filter((d) => d.title.toLowerCase().includes(q));
    },

    statusMeta(status) {
      const meta = {
        queued: { label: 'Queued', className: 'bg-surface-hover text-text-muted', dotClass: 'bg-text-subtle' },
        parsing: { label: 'Parsing', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300', dotClass: 'bg-blue-500 animate-pulse' },
        chunking: { label: 'Chunking', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300', dotClass: 'bg-blue-500 animate-pulse' },
        embedding: { label: 'Embedding', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300', dotClass: 'bg-blue-500 animate-pulse' },
        indexed: { label: 'Indexed', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300', dotClass: 'bg-emerald-500' },
        failed: { label: 'Failed', className: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300', dotClass: 'bg-red-500' },
      };
      return meta[status] || meta.queued;
    },

    async loadDocuments() {
      try {
        const resp = await fetch('/api/documents/');
        if (!resp.ok) throw new Error('request failed');
        const data = await resp.json();
        this.documents = Array.isArray(data) ? data : data.results || [];
      } catch (err) {
        this.toast('Could not load the document library.', 'error');
      } finally {
        this.documentsLoading = false;
        this.schedulePolling();
      }
    },

    hasPendingDocuments() {
      const pending = ['queued', 'parsing', 'chunking', 'embedding'];
      return this.documents.some((d) => pending.includes(d.status));
    },

    schedulePolling() {
      if (this.pollHandle) clearTimeout(this.pollHandle);
      if (!this.hasPendingDocuments()) return;
      this.pollHandle = setTimeout(() => this.loadDocuments(), 2500);
    },

    onFileInputChange(e) {
      this.uploadFiles(e.target.files);
      e.target.value = '';
    },

    onFileDrop(e) {
      this.dragActive = false;
      this.uploadFiles(e.dataTransfer.files);
    },

    uploadFiles(fileList) {
      const files = Array.from(fileList || []);
      if (files.length === 0) return;

      const formData = new FormData();
      files.forEach((f) => formData.append('files', f));

      const entry = Alpine.reactive({
        id: 'upload-' + Date.now() + '-' + Math.random().toString(36).slice(2),
        filename: files.length === 1 ? files[0].name : `${files.length} files`,
        progress: 0,
      });
      this.uploads.push(entry);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/documents/upload/files/');

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) entry.progress = Math.round((e.loaded / e.total) * 100);
      };

      xhr.onload = () => {
        this.uploads = this.uploads.filter((u) => u.id !== entry.id);
        if (xhr.status >= 200 && xhr.status < 300) {
          let results = [];
          try {
            results = JSON.parse(xhr.responseText);
          } catch (parseErr) {
            // fall through with an empty results array
          }
          const errors = results.filter((r) => r.error);
          const duplicates = results.filter((r) => r.duplicate);
          errors.forEach((r) => this.toast(`${r.filename}: ${r.error}`, 'error'));
          if (duplicates.length > 0) {
            this.toast(`${duplicates.length} file(s) were already uploaded.`, 'info');
          }
          this.loadDocuments();
        } else {
          this.toast('Upload failed. Please try again.', 'error');
        }
      };

      xhr.onerror = () => {
        this.uploads = this.uploads.filter((u) => u.id !== entry.id);
        this.toast('Upload failed — check your connection.', 'error');
      };

      xhr.send(formData);
    },

    async deleteDocument(id) {
      if (!window.confirm('Delete this document? This cannot be undone.')) return;
      try {
        const resp = await fetch(`/api/documents/${id}/`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('request failed');
        this.documents = this.documents.filter((d) => d.id !== id);
      } catch (err) {
        this.toast('Could not delete the document.', 'error');
      }
    },

    async reindexDocument(id) {
      try {
        const resp = await fetch(`/api/documents/${id}/reindex/`, { method: 'POST' });
        if (!resp.ok) throw new Error('request failed');
        const updated = await resp.json();
        const idx = this.documents.findIndex((d) => d.id === id);
        if (idx !== -1) this.documents[idx] = updated;
        this.schedulePolling();
        // Stale chunks from before the re-index shouldn't linger if expanded.
        delete this.chunksByDocument[id];
      } catch (err) {
        this.toast('Could not re-index the document.', 'error');
      }
    },

    async toggleDocumentChunks(id) {
      if (this.expandedDocumentId === id) {
        this.expandedDocumentId = null;
        return;
      }
      this.expandedDocumentId = id;
      if (this.chunksByDocument[id]) return; // already loaded

      const entry = Alpine.reactive({ loading: true, error: null, items: [] });
      this.chunksByDocument[id] = entry;
      try {
        const resp = await fetch(`/api/documents/${id}/chunks/`);
        if (!resp.ok) throw new Error('request failed');
        entry.items = await resp.json();
      } catch (err) {
        entry.error = 'Could not load chunks for this document.';
      } finally {
        entry.loading = false;
      }
    },

    // ---------------------------------------------------------------
    // Models
    // ---------------------------------------------------------------
    async loadModels() {
      try {
        const resp = await fetch('/api/chat/models/');
        if (!resp.ok) throw new Error('request failed');
        this.models = await resp.json();
        const current = this.models.find((m) => m.name === this.selectedModel);
        if (!current) {
          const installedDefault = this.models.find((m) => m.is_default && m.installed);
          const firstInstalled = this.models.find((m) => m.installed);
          this.selectedModel = (installedDefault || firstInstalled || this.models[0] || {}).name || null;
        }
      } catch (err) {
        this.toast('Could not reach the server to list models.', 'error');
      }
    },

    // ---------------------------------------------------------------
    // Sessions
    // ---------------------------------------------------------------
    async loadSessions() {
      try {
        const resp = await fetch('/api/chat/sessions/');
        if (!resp.ok) throw new Error('request failed');
        const data = await resp.json();
        this.sessions = Array.isArray(data) ? data : data.results || [];
      } catch (err) {
        // Non-critical: the history popover just stays empty.
      }
    },

    newChat() {
      this.currentSessionId = null;
      this.messages = [];
      this.lastCitations = [];
      this.highlightedRank = null;
      this.$nextTick(() => document.getElementById('composer')?.focus());
    },

    async selectSession(id) {
      this.currentSessionId = id;
      this.messagesLoading = true;
      this.messages = [];
      this.lastCitations = [];
      this.highlightedRank = null;
      try {
        const resp = await fetch(`/api/chat/sessions/${id}/`);
        if (!resp.ok) throw new Error('request failed');
        const data = await resp.json();
        this.messages = (data.messages || []).map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          citations: m.citations || [],
          streaming: false,
          model_used: m.model_used,
          latency_ms: m.latency_ms,
        }));
        const lastAssistant = [...this.messages].reverse().find((m) => m.role === 'assistant');
        this.lastCitations = lastAssistant ? lastAssistant.citations : [];
      } catch (err) {
        this.toast('Could not load that chat.', 'error');
      } finally {
        this.messagesLoading = false;
        this.$nextTick(() => this.scrollChatToBottom());
      }
    },

    async createSession() {
      try {
        const resp = await fetch('/api/chat/sessions/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!resp.ok) throw new Error('request failed');
        return await resp.json();
      } catch (err) {
        this.toast('Could not start a new chat.', 'error');
        return null;
      }
    },

    // ---------------------------------------------------------------
    // Chat / streaming
    // ---------------------------------------------------------------
    scrollChatToBottom() {
      const el = document.getElementById('chat-scroll');
      if (el) el.scrollTop = el.scrollHeight;
    },

    escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    },

    renderAnswer(msg) {
      const escaped = this.escapeHtml(msg.content || '');
      return escaped.replace(/\[(\d+)\]/g, (match, n) => {
        return `<button type="button" class="citation-chip" data-citation-rank="${n}">[${n}]</button>`;
      });
    },

    onChatClick(e) {
      const target = e.target.closest('[data-citation-rank]');
      if (!target) return;
      this.highlightCitation(parseInt(target.dataset.citationRank, 10));
    },

    highlightCitation(rank) {
      this.sourcesOpen = true;
      this.highlightedRank = rank;
      this.$nextTick(() => {
        document.getElementById('citation-' + rank)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      setTimeout(() => {
        if (this.highlightedRank === rank) this.highlightedRank = null;
      }, 2500);
    },

    async sendMessage() {
      const question = this.composerText.trim();
      if (!question || this.streaming) return;
      if (!this.selectedModel) {
        this.toast('Select a model first.', 'error');
        return;
      }

      this.composerText = '';
      this.streaming = true;

      if (!this.currentSessionId) {
        const session = await this.createSession();
        if (!session) {
          this.streaming = false;
          this.composerText = question;
          return;
        }
        this.currentSessionId = session.id;
        this.sessions.unshift(session);
      }

      this.messages.push(Alpine.reactive({ id: 'local-' + Date.now(), role: 'user', content: question, citations: [] }));

      const assistantMsg = Alpine.reactive({
        id: 'pending',
        role: 'assistant',
        content: '',
        citations: [],
        streaming: true,
        model_used: this.selectedModel,
        latency_ms: null,
      });
      this.messages.push(assistantMsg);
      this.$nextTick(() => this.scrollChatToBottom());

      try {
        const resp = await fetch(`/api/chat/sessions/${this.currentSessionId}/messages/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, model: this.selectedModel }),
        });

        if (!resp.ok || !resp.body) {
          throw new Error('Request failed with status ' + resp.status);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const rawEvent = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            this.handleSSEEvent(rawEvent, assistantMsg);
          }
        }
      } catch (err) {
        if (!assistantMsg.content) {
          assistantMsg.content = 'Something went wrong while generating a response.';
        }
        this.toast('Connection to the server was interrupted.', 'error');
      } finally {
        assistantMsg.streaming = false;
        this.streaming = false;
        this.loadSessions();
        this.$nextTick(() => this.scrollChatToBottom());
      }
    },

    handleSSEEvent(rawEvent, assistantMsg) {
      let eventType = 'message';
      const dataLines = [];
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) return;

      let data;
      try {
        data = JSON.parse(dataLines.join('\n'));
      } catch (parseErr) {
        return;
      }

      if (eventType === 'token') {
        assistantMsg.content += data;
        this.$nextTick(() => this.scrollChatToBottom());
      } else if (eventType === 'done') {
        assistantMsg.id = data.message_id;
        assistantMsg.citations = data.citations || [];
        this.lastCitations = assistantMsg.citations;
      } else if (eventType === 'error') {
        this.toast(data.detail || 'An error occurred while generating the response.', 'error');
        if (!assistantMsg.content) {
          assistantMsg.content = data.detail || 'An error occurred.';
        }
      }
      // 'query_rewrite' events are received but not surfaced in the UI.
    },

    // ---------------------------------------------------------------
    // Toasts
    // ---------------------------------------------------------------
    toast(message, type = 'error') {
      const id = ++this.toastSeq;
      this.toasts.push({ id, message, type });
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id);
      }, 5000);
    },
  }));
});
