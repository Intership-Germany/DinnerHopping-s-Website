// Admin Email Templates Management
(function(){
  const listEl = document.getElementById('templateList');
  const editor = document.getElementById('editor');
  const statusEl = document.getElementById('status');
  const fields = {
    key: document.getElementById('tplKey'),
    subject: document.getElementById('tplSubject'),
    description: document.getElementById('tplDescription'),
    vars: document.getElementById('tplVars'),
    html: document.getElementById('tplHtml')
  };
  let currentKey = null;

  async function loadList(){
    listEl.innerHTML = '<li>Loading...</li>';
    try {
      const res = await fetch('/admin/email-templates');
      if(!res.ok){ listEl.innerHTML = '<li>Error loading list</li>'; return; }
      const data = await res.json();
      listEl.innerHTML = '';
      data.forEach(t => {
        const li = document.createElement('li');
        li.textContent = t.key;
        if(t.key === currentKey) li.classList.add('active');
        li.onclick = () => openTemplate(t.key);
        listEl.appendChild(li);
      });
    } catch(err){
      listEl.innerHTML = '<li>Network error</li>';
    }
  }

  function newTemplate(){
    currentKey = null;
    editor.style.display='block';
    document.getElementById('editorTitle').textContent = 'New Template';
    fields.key.value='';
    fields.subject.value='';
    fields.description.value='';
    fields.vars.value='';
    fields.html.value='';
    statusEl.textContent='';
    statusEl.style.color='';
  }

  async function openTemplate(key){
    try {
      const res = await fetch('/admin/email-templates/' + encodeURIComponent(key));
      if(!res.ok){ statusEl.textContent='Failed to load template'; statusEl.style.color='#b00020'; return; }
      const t = await res.json();
      currentKey = t.key;
      editor.style.display='block';
      document.getElementById('editorTitle').textContent = 'Edit: ' + t.key;
      fields.key.value = t.key;
      fields.subject.value = t.subject || '';
      fields.description.value = t.description || '';
      fields.vars.value = (t.variables || []).join(', ');
      fields.html.value = t.html_body || '';
      statusEl.textContent='';
      statusEl.style.color='';
      await loadList();
    } catch(err){
      statusEl.textContent='Network error loading template';
      statusEl.style.color='#b00020';
    }
  }

  async function save(){
    const payload = {
      key: fields.key.value.trim(),
      subject: fields.subject.value,
      description: fields.description.value || null,
      variables: fields.vars.value.split(',').map(v=>v.trim()).filter(Boolean),
      html_body: fields.html.value
    };
    if(!payload.key){ statusEl.textContent='Key required'; statusEl.style.color='#b00020'; return; }
    const method = currentKey ? 'PUT' : 'POST';
    const url = currentKey ? '/admin/email-templates/' + encodeURIComponent(currentKey) : '/admin/email-templates';
    try {
      const res = await fetch(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      if(res.ok){
        statusEl.textContent='Saved';
        statusEl.style.color='#0a7a2a';
        currentKey = payload.key;
        loadList();
      } else {
        const data = await res.json().catch(()=>({}));
        statusEl.textContent=data.detail || 'Save failed';
        statusEl.style.color='#b00020';
      }
    } catch(err){
      statusEl.textContent='Network error during save';
      statusEl.style.color='#b00020';
    }
  }

  async function del(){
    if(!currentKey){ editor.style.display='none'; return; }
    if(!confirm('Delete template ' + currentKey + '?')) return;
    try {
      const res = await fetch('/admin/email-templates/' + encodeURIComponent(currentKey), { method:'DELETE' });
      if(res.ok){
        statusEl.textContent='Deleted';
        statusEl.style.color='#0a7a2a';
        editor.style.display='none';
        currentKey=null;
        loadList();
      } else {
        statusEl.textContent='Delete failed';
        statusEl.style.color='#b00020';
      }
    } catch(err){
      statusEl.textContent='Network error during delete';
      statusEl.style.color='#b00020';
    }
  }

  // Expose for debug (optional)
  window.__EmailTplAdmin = { reload: loadList, newTemplate };

  document.getElementById('newBtn').onclick = newTemplate;
  document.getElementById('saveBtn').onclick = save;
  document.getElementById('deleteBtn').onclick = del;
  document.getElementById('cancelBtn').onclick = () => { editor.style.display='none'; };

  loadList();
})();
