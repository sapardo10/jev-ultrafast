(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // A control with no accessible name (an icon-only hamburger) is still reachable: say where it is, what it
  // controls, and its icon class. These are observed attributes, never selectors or coordinates for the model.
  const fallbackName = (e,r,kind) => {
    const vertical=r.y+r.height/2<innerHeight*0.2 ? 'top' : r.y+r.height/2>innerHeight*0.8 ? 'bottom' : 'middle';
    const horizontal=r.x+r.width/2<innerWidth*0.33 ? 'left' : r.x+r.width/2>innerWidth*0.67 ? 'right' : 'center';
    const controls=(e.getAttribute('aria-controls')||'').trim().split(/\s+/)[0];
    const icon=[...e.querySelectorAll('[class]')].flatMap(n=>[...n.classList])
      .filter(c=>/^(fa-|bi-|mdi-|icon-|material-)/.test(c) && !/^fa-(solid|regular|brands)$/.test(c))[0];
    const small=r.width*r.height < innerWidth*innerHeight/8;
    return !small ? kind : 'Unnamed icon '+kind+' ('+vertical+' '+horizontal+' of the screen'+
      (controls ? ', controls '+controls : '')+(icon ? ', icon '+icon : '')+')';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||fallbackName(e,r,rname),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) {
    // Name what lies below so a decision model knows scrolling reaches e.g. Next or Submit.
    const below=[];
    for (const e of document.querySelectorAll(selector)) {
      const r=e.getBoundingClientRect(), label=safe(e) && visible(e) && role(e) && name(e);
      if (label && r.width>0 && r.height>0 && r.top>=innerHeight) below.push({label:label.trim().slice(0,24),top:r.top,
        rank:(e.matches('[rel~="next"],[rel~="prev"],nav *,[role="navigation"] *,.pagination *,.pager *,'+
          'button,[type="submit"],[role="button"]') ? 0 : 1)*1e6+r.top});
    }
    below.sort((a,b)=>a.rank-b.rank);
    const key=below.filter(b=>b.rank<1e6), names=[...new Set((key.length ? key : below).map(b=>b.label))].slice(0,4);
    // With a key control below, scrolling lands on it; otherwise page by most of a viewport.
    const delta=key.length ? Math.max(120,key[0].top-innerHeight*0.6) : innerHeight*0.85;
    actions.push({id:'scroll_down',kind:'scroll',delta:Math.round(delta),
      label:'Scroll down'+(names.length ? ' to reach: '+names.join(', ') : '')});
  }
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-Math.round(innerHeight*0.85)});
  // Map apps keep the document fixed and scroll a side panel; wheel input must land on that panel.
  let panel=null, panelArea=0;
  for (const e of document.querySelectorAll('div,section,aside,nav,ul,main')) {
    if (e.scrollHeight<=e.clientHeight+2 || !['auto','scroll'].includes(getComputedStyle(e).overflowY) || !visible(e)) continue;
    const r=e.getBoundingClientRect();
    const w=Math.max(0,Math.min(r.right,innerWidth)-Math.max(r.left,0));
    const h=Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
    if (w*h>panelArea) { panelArea=w*h; panel=e; }
  }
  if (panel) {
    const r=panel.getBoundingClientRect();
    const at={x:Math.round(Math.max(r.left,0)+Math.min(r.width,innerWidth-Math.max(r.left,0))/2),
      y:Math.round(Math.max(r.top,0)+Math.min(r.height,innerHeight-Math.max(r.top,0))/2)};
    const step=Math.round(panel.clientHeight*0.7);
    if (!actions.some(a=>a.id==='scroll_down') && panel.scrollTop+panel.clientHeight<panel.scrollHeight-2)
      actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down the panel for more options',delta:step,at});
    if (!actions.some(a=>a.id==='scroll_up') && panel.scrollTop>0)
      actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up the panel',delta:-step,at});
  }
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
