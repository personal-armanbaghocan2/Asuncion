/* Offline charts. No dependencies, network requests, or generated sample values. */
(() => {
  'use strict';
  const source = document.getElementById('dashboard-data');
  if (!source) return;
  const d = JSON.parse(source.textContent), ns = 'http://www.w3.org/2000/svg';
  const colors = ['#428fff','#22a568','#ffbf32','#ff9a54','#ff4865','#aa7aff','#60d6ef','#96aaba','#96aaba'];
  const el = (name, attrs = {}, text) => {
    const node = document.createElementNS(ns, name);
    Object.entries(attrs).forEach(([k,v]) => node.setAttribute(k,v));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  function svg(container, width, height, description) {
    const root = el('svg', {viewBox:`0 0 ${width} ${height}`, role:'img', 'aria-label':description});
    root.append(el('title', {}, description));
    document.getElementById(container).replaceChildren(root);
    return root;
  }
  function axes(root, max, width, height, bottom = 24) {
    const top = 16, left = 27, right = width - 5, floor = height - bottom;
    const step = Math.max(1, Math.ceil(max / 4)), cap = step * 4;
    for (let i=0; i<=4; i++) {
      const y = floor - (floor-top)*i/4;
      root.append(el('line', {x1:left,y1:y,x2:right,y2:y,stroke:'#edf1f6','stroke-width':1}));
      root.append(el('text', {x:left-8,y:y+3,'text-anchor':'end',fill:'#172061','font-size':10},i*step));
    }
    return {left,right,floor,top,cap};
  }
  function bars(id, rows, width, height, palette) {
    const root = svg(id,width,height,rows.map(r=>`${r.label}: ${r.count}${r.kind ? ' '+r.kind : ''}`).join('; '));
    const a = axes(root,Math.max(0,...rows.map(r=>r.count)),width,height,25);
    const slot = (a.right-a.left)/rows.length;
    rows.forEach((r,i) => {
      const h = (a.floor-a.top)*r.count/a.cap, x = a.left+slot*i+slot*.17;
      const rect = el('rect', {x,y:a.floor-h,width:slot*.66,height:h,rx:2,fill:palette[i%palette.length]});
      rect.append(el('title', {},`${r.label}: ${r.count}${r.kind ? ' '+r.kind : ''}`)); root.append(rect);
      root.append(el('text',{x:x+slot*.33,y:a.floor-h-5,'text-anchor':'middle',fill:'#090f50','font-size':11,'font-weight':700},r.count));
      root.append(el('text',{x:x+slot*.33,y:a.floor+17,'text-anchor':'middle',fill:'#172061','font-size':rows.length>4?10:9},r.label));
    });
  }
  function donut(id, rows, percentagesOnly = false) {
    const total = rows.reduce((s,r)=>s+r.count,0), chart = document.getElementById(id+'-chart');
    let angle = 0;
    const stops = rows.filter(r=>r.count).map(r => {
      const start = angle; angle += r.count/total*360;
      return `${r.color} ${start}deg ${angle}deg`;
    });
    chart.style.background = total ? `conic-gradient(${stops.join(',')})` : '#edf1f5';
    chart.setAttribute('aria-label',`${total} total. ${rows.map(r=>`${r.label}: ${r.count} (${r.percent}%)`).join('; ')}`);
    document.getElementById(id+'-total').textContent = total.toLocaleString('en-US');
    const legend = document.getElementById(id+'-legend'); legend.replaceChildren();
    rows.forEach(r=>{
      const li = document.createElement('li'), dot = document.createElement('span'), label = document.createElement('span'), value = document.createElement('span');
      dot.className='dash-dot'; dot.style.background=r.color;
      label.className='legend-label'; label.textContent=r.label;
      value.className='legend-value'; value.textContent=percentagesOnly ? `${r.percent}%` : `${r.count} (${r.percent}%)`;
      li.title=`${r.label}: ${r.count} records (${r.percent}%)`;
      li.append(dot,label,value); legend.append(li);
    });
    if (!total) {const empty=document.createElement('li');empty.textContent='No records';legend.append(empty);}
  }
  bars('pipeline-chart',d.pipeline,650,136,colors);
  bars('nlp-chart',d.nlp,350,126,d.nlp.map(r=>r.color));
  donut('status',d.statuses.all);donut('category',d.inventory.categories,true);donut('stock',d.inventory.stock,true);
  document.getElementById('status-module').addEventListener('change',e=>donut('status',d.statuses[e.target.value]));
  const monthlyColors=['#428fff','#22a568','#ffbf32','#ff4865','#96aaba'];
  // Flask JSON sorts object keys; preserve the approved series/legend order.
  const monthlyEntries=['PPMP','APP','PR','PO','Others'].map(key=>[key,d.monthly[key]]);
  const totals=Array.from({length:12},(_,i)=>Object.values(d.monthly).reduce((s,values)=>s+values[i],0));
  const chart=svg('monthly-chart',350,112,`${d.year} documents created per month. Total line: ${totals.join(', ')}. `+Object.entries(d.monthly).map(([key,values])=>`${key}: ${values.join(', ')}`).join('; '));
  const a=axes(chart,Math.max(...totals),350,112,22), slot=(a.right-a.left)/12, points=[];
  monthlyEntries.forEach(([label,values],series)=>{
    const item=document.createElement('span'), dot=document.createElement('i');dot.className='dash-dot';dot.style.background=monthlyColors[series];item.append(dot,document.createTextNode(label));document.getElementById('monthly-legend').append(item);
  });
  totals.forEach((total,i)=>{
    const x=a.left+slot*(i+.5); let base=a.floor;
    monthlyEntries.forEach(([label,values],series)=>{
      const h=(a.floor-a.top)*values[i]/a.cap;
      const rect=el('rect',{x:x-slot*.27,y:base-h,width:slot*.54,height:h,fill:monthlyColors[series]});rect.append(el('title',{},`${label}: ${values[i]}`));chart.append(rect);base-=h;
    });
    const y=a.floor-(a.floor-a.top)*total/a.cap;points.push([x,y]);
    chart.append(el('text',{x,y:a.floor+16,'text-anchor':'middle','font-size':8,fill:'#172061'},['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][i]));
  });
  chart.append(el('polyline',{points:points.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#169b5d','stroke-width':2.4}));
  points.forEach(([x,y],i)=>{const dot=el('circle',{cx:x,cy:y,r:3.3,fill:'#169b5d'});dot.append(el('title',{},`Total: ${totals[i]}`));chart.append(dot);});
  const all=document.getElementById('show-all-reviews');
  function showReviews(e) {e?.preventDefault(); const more=document.getElementById('more-reviews');if(more){more.hidden=!more.hidden;all?.setAttribute('aria-expanded',String(!more.hidden));}}
  all?.setAttribute('aria-controls','more-reviews');all?.setAttribute('aria-expanded','false');all?.addEventListener('click',showReviews);
  const viewAll=document.querySelector('#pending-reviews .dash-heading>a');
  viewAll.href='#pending-reviews';viewAll.addEventListener('click',e=>{if(all)showReviews(e);});
  document.querySelectorAll('.dash-table-scroll').forEach(wrap=>{
    wrap.tabIndex=0;wrap.setAttribute('role','region');
    wrap.setAttribute('aria-label',wrap.closest('.dash-panel').querySelector('h3').textContent.trim()+' table; scroll for more columns');
  });
})();
