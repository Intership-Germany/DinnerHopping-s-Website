// Lightweight modal dialog utility for admin pages
(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  if (window.dh.dialog) return;

  const toneStyles = {
    info: { accent: '#2563eb', icon: 'ℹ️', title: '#1f2937' },
    success: { accent: '#059669', icon: '✅', title: '#065f46' },
    warning: { accent: '#f59e0b', icon: '⚠️', title: '#92400e' },
    danger: { accent: '#dc2626', icon: '⛔', title: '#991b1b' },
  };
  const DEFAULT_TONE = 'info';
  const rootId = 'dh-dialog-root';
  let openCount = 0;

  function ensureRoot(){
    let root = document.getElementById(rootId);
    if (!root){
      root = document.createElement('div');
      root.id = rootId;
      root.className = 'dh-dialog-root';
      document.body.appendChild(root);
    }
    return root;
  }

  function lockBody(){
    openCount += 1;
    if (openCount === 1){
      document.body.dataset.dhDialogScroll = document.body.style.overflow || '';
      document.body.style.overflow = 'hidden';
    }
  }

  function unlockBody(){
    openCount = Math.max(0, openCount - 1);
    if (openCount === 0){
      const existing = document.body.dataset.dhDialogScroll;
      document.body.style.overflow = existing || '';
      delete document.body.dataset.dhDialogScroll;
    }
  }

  function normalizeMessage(message){
    if (message == null) return '';
    if (Array.isArray(message)) return message.filter(Boolean).join('\n');
    return String(message);
  }

  function appendParagraph(container, text){
    if (!text) return;
    const p = document.createElement('p');
    p.className = 'text-sm leading-snug text-[#1f2937]';
    p.textContent = text;
    container.appendChild(p);
  }

  function appendListItem(list, text){
    const li = document.createElement('li');
    li.className = 'leading-snug';
    li.textContent = text;
    list.appendChild(li);
  }

  function ensureList(container, currentList){
    if (currentList && currentList.isConnected) return currentList;
    const list = document.createElement('ul');
    list.className = 'ml-4 list-disc space-y-1 text-xs text-[#1f2937]';
    container.appendChild(list);
    return list;
  }

  function buildMessageElement(message){
    const text = normalizeMessage(message);
    const content = document.createElement('div');
    content.className = 'space-y-2 text-sm leading-relaxed text-[#1f2937]';
    const blocks = text.split(/\n{2,}/);
    blocks.forEach((block)=>{
      const lines = block.split('\n');
      let listEl = null;
      lines.forEach((line)=>{
        const trimmed = line.trim();
        if (!trimmed){
          listEl = null;
          return;
        }
        const bulletMatch = trimmed.match(/^([-*•])\s+(.*)$/);
        if (bulletMatch){
          listEl = ensureList(content, listEl);
          appendListItem(listEl, bulletMatch[2].trim());
          return;
        }
        listEl = null;
        appendParagraph(content, trimmed);
      });
    });
    if (!content.children.length){
      appendParagraph(content, text);
    }
    return content;
  }

  function buildTitleElement(label, tone){
    if (!label) return null;
    const titleWrap = document.createElement('div');
    titleWrap.className = 'flex items-center gap-3';
    const toneInfo = toneStyles[tone] || toneStyles[DEFAULT_TONE];
    if (toneInfo && toneInfo.icon){
      const icon = document.createElement('span');
      icon.className = 'text-xl';
      icon.textContent = toneInfo.icon;
      titleWrap.appendChild(icon);
    }
    const title = document.createElement('h2');
    title.className = 'text-lg font-semibold';
    title.style.color = (toneStyles[tone] && toneStyles[tone].title) || '#1f2937';
    title.textContent = label;
    titleWrap.appendChild(title);
    return titleWrap;
  }

  function applyEntrance(overlay, panel){
    requestAnimationFrame(()=>{
      overlay.classList.remove('opacity-0');
      overlay.classList.add('opacity-100');
      panel.classList.remove('opacity-0','scale-95');
      panel.classList.add('opacity-100','scale-100');
    });
  }

  function closeDialog(overlay, panel, resolve, result, onKeydown){
    if (!overlay || overlay.dataset.dhClosing) return;
    overlay.dataset.dhClosing = '1';
    overlay.classList.remove('opacity-100');
    overlay.classList.add('opacity-0');
    panel.classList.remove('opacity-100','scale-100');
    panel.classList.add('opacity-0','scale-95');
    if (typeof onKeydown === 'function'){
      document.removeEventListener('keydown', onKeydown);
    }
    setTimeout(()=>{
      try { overlay.remove(); } catch (_) {}
      unlockBody();
      resolve(result);
    }, 160);
  }

  function showDialog(options){
    const opts = Object.assign({
      title: 'Notification',
      message: '',
      tone: DEFAULT_TONE,
      confirmLabel: 'OK',
      cancelLabel: null,
      destructive: false,
      closeOnOverlay: true,
    }, options || {});
    const tone = toneStyles[opts.tone] ? opts.tone : DEFAULT_TONE;

    return new Promise((resolve)=>{
      const root = ensureRoot();
      const overlay = document.createElement('div');
      overlay.className = 'fixed inset-0 z-[1200] flex items-center justify-center px-4 py-8 bg-black/40 backdrop-blur-sm opacity-0 transition-opacity duration-150';
      const panel = document.createElement('div');
      panel.className = 'relative w-full max-w-md rounded-2xl border border-[#e2e8f0] bg-white shadow-2xl opacity-0 scale-95 transition-all duration-150 overflow-hidden';

      const accent = document.createElement('div');
      accent.className = 'absolute inset-x-0 top-0 h-1';
      accent.style.backgroundColor = (toneStyles[tone] && toneStyles[tone].accent) || toneStyles[DEFAULT_TONE].accent;
      panel.appendChild(accent);

      const inner = document.createElement('div');
      inner.className = 'px-6 pt-6 pb-4 space-y-4';
      const header = buildTitleElement(opts.title, tone);
      if (header) inner.appendChild(header);
      inner.appendChild(buildMessageElement(opts.message));
      panel.appendChild(inner);

      const actions = document.createElement('div');
      actions.className = 'px-6 pb-6 pt-2 flex justify-end gap-2';

      let cancelBtn = null;
      if (opts.cancelLabel){
        cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'px-4 py-2 rounded-xl border border-[#cbd5f5] bg-white text-sm font-semibold text-[#1f2937] shadow-sm hover:bg-[#f8fafc] transition focus:outline-none focus:ring-2 focus:ring-[#cbd5f5] focus:ring-offset-1';
        cancelBtn.textContent = opts.cancelLabel;
        actions.appendChild(cancelBtn);
      }

      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      const confirmBase = opts.destructive ? 'bg-[#dc2626] hover:bg-[#b91c1c] focus:ring-[#fecaca]' : 'bg-[#f46f47] hover:bg-[#ff8c42] focus:ring-[#fed7aa]';
      confirmBtn.className = `px-4 py-2 rounded-xl text-sm font-semibold text-white shadow-sm transition focus:outline-none focus:ring-2 focus:ring-offset-1 ${confirmBase}`;
      confirmBtn.textContent = opts.confirmLabel || 'OK';
      actions.appendChild(confirmBtn);

      panel.appendChild(actions);
      overlay.appendChild(panel);
      root.appendChild(overlay);
      lockBody();
      applyEntrance(overlay, panel);

      function onKeydown(ev){
        if (ev.key === 'Escape'){
          if (cancelBtn){
            ev.preventDefault();
            closeDialog(overlay, panel, resolve, false, onKeydown);
          }
        } else if (ev.key === 'Enter'){
          if (document.activeElement === confirmBtn){
            ev.preventDefault();
            closeDialog(overlay, panel, resolve, true, onKeydown);
          }
        }
      }

      document.addEventListener('keydown', onKeydown);

      confirmBtn.addEventListener('click', ()=>{
        closeDialog(overlay, panel, resolve, true, onKeydown);
      });
      if (cancelBtn){
        cancelBtn.addEventListener('click', ()=>{
          closeDialog(overlay, panel, resolve, false, onKeydown);
        });
        if (opts.closeOnOverlay){
          overlay.addEventListener('click', (ev)=>{
            if (ev.target === overlay){
              closeDialog(overlay, panel, resolve, false, onKeydown);
            }
          });
        }
      }
      try {
        confirmBtn.focus({ preventScroll: true });
      } catch (_){
        try { confirmBtn.focus(); } catch(__) {}
      }
    });
  }

  function alert(message, options){
    const opts = Object.assign({}, options, { message, cancelLabel: null });
    if (!opts.title) opts.title = 'Notification';
    return showDialog(opts).then(()=> undefined);
  }

  function confirm(message, options){
    const opts = Object.assign({ tone: 'warning', confirmLabel: 'Confirm', cancelLabel: 'Cancel' }, options, { message });
    return showDialog(opts).then(result => Boolean(result));
  }

  window.dh.dialog = { alert, confirm, show: showDialog };
})();
