/* Original four-page runtime: top-level links enter the cached dashboard;
   embedded pages continue to use the original live JSON endpoints. */
(() => {
  'use strict';
  const names = ['index.html','margin.html','futures-preview.html','risk-preview.html','market-cycle.html'];
  const page = location.pathname.split('/').pop() || 'index.html';
  if (!names.includes(page)) return;
  const embedded = new URLSearchParams(location.search).get('view') === 'embedded';
  if (window === window.top && !embedded) {
    const target = new URL('dashboard.html', location.href);
    target.search = location.search;
    if(page==='margin.html'&&location.hash==='#shareFlows')target.searchParams.set('section','shareFlows');target.hash = page;
    document.documentElement.style.visibility = 'hidden';
    location.replace(target.href);
    return;
  }
  if (window === window.top || !embedded) return;
  let ready = false;
  const origin = location.origin;
  const post = data => parent.postMessage(data, origin);
  function sizeCharts() {
    document.querySelectorAll('[_echarts_instance_]').forEach(el => {
      const chart = window.echarts?.getInstanceByDom(el);
      if (!chart || el.clientWidth <= 0) return;
      if (Math.abs(chart.getWidth()-el.clientWidth)>1 || Math.abs(chart.getHeight()-el.clientHeight)>1)
        chart.resize({width:el.clientWidth,height:el.clientHeight});
      chart.getZr().flush();
    });
  }
  function complete() {
    if (document.querySelector('#rb')?.disabled) return false;
    if (page === 'index.html') return !!document.querySelector('#tb tr') && document.querySelector('#ds')?.options.length>0;
    if (page === 'margin.html') return !!document.querySelector('#marginKpis .kpi') && document.querySelector('#marginDate')?.options.length>0;
    if (page === 'futures-preview.html') return !!document.querySelector('#icDynamic .model') && document.querySelector('#icDate')?.options.length>0;
    if (page === 'market-cycle.html') return !!document.querySelector('#metrics tr') || document.querySelector('#st')?.textContent==='暂不能判断';
    return !!document.querySelector('#grid .mrow');
  }
  function report() {
    if (ready || !complete()) return;
    ready = true;
    setTimeout(() => { sizeCharts();post({kind:'view-ready',page,width:innerWidth,height:innerHeight}); },0);
  }
  document.addEventListener('click', event => {
    const a = event.target.closest('a.view-tab');
    if (!a) return;
    const target = new URL(a.href);
    const name = target.pathname.split('/').pop();
    if (target.origin !== origin || !names.includes(name)) return;
    event.preventDefault();event.stopImmediatePropagation();
    post({kind:'navigate',page:name,sentAt:Date.now()});
  }, true);
  addEventListener('message',event => {
    if(event.source!==parent || event.origin!==origin) return;
    if(event.data?.kind==='view-visibility' && event.data.page===page && event.data.visible && !document.hidden && ['index.html','risk-preview.html','market-cycle.html'].includes(page)) {
      if(page!=='index.html' || typeof view==='undefined' || view==='live') {
        if(typeof refresh==='function')refresh();
      }
    }
    if(event.data?.kind==='prepare-view') {
      sizeCharts();post({kind:'view-sized',page,width:innerWidth,height:innerHeight});
    }
  });
  document.addEventListener('DOMContentLoaded',() => {
    document.querySelectorAll('.view-tabs').forEach(nav=>{
      if(nav.querySelector('a[href="market-cycle.html"]'))return;
      const a=document.createElement('a');a.className='view-tab';a.href='market-cycle.html';
      a.innerHTML='<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12c2.5 0 2.5-7 5.5-7s2.5 10 5.5 10 2.5-7 5-7"/><circle cx="7.5" cy="5" r="1.25" fill="currentColor" stroke="none"/><circle cx="13" cy="15" r="1.25" fill="currentColor" stroke="none"/></svg><span>市场顶底</span>';nav.append(a);
    });
    document.querySelector('a.view-tab[aria-current="page"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
    new MutationObserver(report).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
    report();
    if(page==='margin.html'&&new URLSearchParams(location.search).get('section')==='shareFlows')document.getElementById('shareFlows')?.scrollIntoView({block:'start'});
    // Errors stay on the original page. Never leave a requested tab waiting forever.
    setTimeout(() => {if(!ready){ready=true;sizeCharts();post({kind:'view-ready',page,width:innerWidth,height:innerHeight});}},14000);
  });
  addEventListener('load',report);
})();
