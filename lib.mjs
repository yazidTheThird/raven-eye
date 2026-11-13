export const STATUS={available:'Available',presale:'Presale',upcoming:'Coming soon',backorder:'Backorder',out_of_stock:'Out of stock',unknown:'Unconfirmed'};
export const CHANGE={new:'Newly detected',available:'Now available',restock:'Back in stock',sold_out:'Went out of stock',price_drop:'Price decreased',price_up:'Price increased',status_change:'Status changed'};
export const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
export function safeURL(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:''}catch{return ''}}
export const dateValue=v=>Number.isFinite(Date.parse(v))?Date.parse(v):0;
export const searchable=p=>[p.title,p.sourceName,p.description,...(p.tags||[])].join(' ').toLowerCase();
export function isRecentChange(p,now=Date.now()){return p.change&&dateValue(p.change.at)>now-7*86400000&&dateValue(p.change.at)<=now+60000;}
export function filterProducts(products,filters,watchlist=new Set(),now=Date.now()){
 let q=(filters.query||'').trim().toLowerCase().split(/\s+/).filter(Boolean);
 return products.filter(p=>{
 if(filters.view==='watchlist'&&!watchlist.has(p.id))return false;
 if(filters.category&&filters.category!=='all'&&p.category!==filters.category)return false;
 if(filters.metal&&filters.metal!=='all'&&p.metal!==filters.metal)return false;
 if(filters.status&&filters.status!=='all'&&p.status!==filters.status)return false;
 if(filters.source&&filters.source!=='all'&&p.sourceId!==filters.source)return false;
 const haystack=searchable(p);if(!q.every(t=>haystack.includes(t)))return false;
 if(filters.lens==='color'&&!/colou?ri[sz]ed|colou?red|art coin|enamel|colou?r print|digital print/.test(haystack))return false;
 if(filters.lens==='limited'&&!(Number.isFinite(p.mintage)&&p.mintage>0&&p.mintage<=500))return false;
 if(filters.lens==='presale'&&!['presale','upcoming','backorder'].includes(p.status))return false;
 if(filters.lens==='new'&&!(p.change?.type==='new'&&isRecentChange(p,now)))return false;
 return true;
 });
}
export function sortProducts(products,sort){return [...products].sort((a,b)=>{
 if(sort==='title')return a.title.localeCompare(b.title);
 if(sort==='mintage')return (a.mintage>0?a.mintage:Infinity)-(b.mintage>0?b.mintage:Infinity)||a.title.localeCompare(b.title);
 if(sort==='firstSeen')return dateValue(b.firstSeenAt)-dateValue(a.firstSeenAt)||dateValue(b.publishedAt)-dateValue(a.publishedAt)||a.title.localeCompare(b.title);
 if(sort==='changed')return dateValue(b.change?.at)-dateValue(a.change?.at)||dateValue(b.publishedAt)-dateValue(a.publishedAt);
 return dateValue(b.publishedAt)-dateValue(a.publishedAt)||dateValue(b.firstSeenAt)-dateValue(a.firstSeenAt)||a.title.localeCompare(b.title);
 });}
export function csvCell(value){let s=String(value??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
export function toCSV(products){const fields=['title','sourceName','metal','category','status','price','currency','weight','mintage','year','publishedAt','firstSeenAt','lastCheckedAt','url'];return '\ufeff'+[fields.map(csvCell).join(','),...products.map(p=>fields.map(k=>csvCell(p[k])).join(','))].join('\r\n');}
