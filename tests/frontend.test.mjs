import test from 'node:test';import assert from 'node:assert/strict';import {filterProducts,sortProducts,csvCell,safeURL,isRecentChange,sourceCoverage,filterSources,listingSources,focusForCategory,colorEvidence} from '../lib.mjs';
const now=Date.parse('2026-11-13T12:00:00Z');const products=[{id:'1',title:'Goat Colorized Coin',sourceId:'a',sourceName:'Mint A',metal:'Silver',category:'Coin',mintage:199,status:'presale',tags:[],publishedAt:'2026-11-12',firstSeenAt:'2026-11-13',change:null},{id:'2',title:'Gold Bar',sourceId:'b',metal:'Gold',category:'Bar',status:'available',mintage:null,tags:[],publishedAt:null,firstSeenAt:'2026-11-13',change:{type:'new',at:'2026-11-13T10:00:00Z'}},{id:'3',title:'Future coin',sourceId:'a',metal:'Silver',category:'Coin',status:'upcoming',mintage:1000,tags:[],publishedAt:'2026-11-11'}];
test('filters compose without confusing baseline with new releases',()=>{assert.deepEqual(filterProducts(products,{lens:'new'},new Set(),now).map(p=>p.id),['2']);assert.deepEqual(filterProducts(products,{query:'goat coin',metal:'Silver',status:'presale',lens:'limited'}).map(p=>p.id),['1']);assert.equal(filterProducts(products,{query:'unfindable'}).length,0);});
test('watchlist and ahead-of-release filters',()=>{assert.deepEqual(filterProducts(products,{view:'watchlist'},new Set(['2'])).map(p=>p.id),['2']);assert.deepEqual(filterProducts(products,{lens:'presale'}).map(p=>p.id),['1','3']);});
test('unknown dates and mintages sort after known values',()=>{assert.equal(sortProducts(products,'published')[0].id,'1');assert.equal(sortProducts(products,'published').at(-1).id,'2');assert.equal(sortProducts(products,'mintage').at(-1).id,'2');});
test('untrusted feed values cannot become script URLs or CSV formulas',()=>{assert.equal(safeURL('javascript:alert(1)'), '');assert.equal(safeURL('https://mint.example/coin'),'https://mint.example/coin');assert.equal(csvCell('=1+2'),'"\'=1+2"');assert.equal(csvCell('Coin, "A"'),'"Coin, ""A"""');});
test('future dates are not recent changes',()=>{assert.equal(Boolean(isRecentChange({change:{at:'2027-01-01'}},now)),false);});
test('reported brand search composes with the actual source type',()=>{const listings=[{...products[0],maker:'Aurora Atelier',sourceKind:'Dealer'},{...products[1],maker:'Aurora Mint',sourceKind:'Private mint'}];assert.deepEqual(filterProducts(listings,{query:'aurora',sourceKind:'Dealer'}).map(p=>p.id),['1']);assert.deepEqual(filterProducts(listings,{query:'aurora mint',sourceKind:'Private mint'}).map(p=>p.id),['2']);assert.equal(filterProducts(listings,{query:'aurora',sourceKind:'Colorizer'}).length,0);});
test('directory entries never inflate automatic source health',()=>{const sources=[{id:'a',name:'Alpha',tracking:'automatic',status:'ok'},{id:'b',name:'Beta',tracking:'automatic',status:'error'},{id:'c',name:'Color Lab',tracking:'directory',status:'directory'},{id:'d',name:'Directory',status:'directory'},{id:'e',name:'Legacy',status:'ok',type:'shopify'}];const coverage=sourceCoverage(sources);assert.deepEqual(coverage.automatic.map(s=>s.id),['a','b','e']);assert.deepEqual(coverage.healthy.map(s=>s.id),['a','e']);assert.deepEqual(coverage.directory.map(s=>s.id),['c','d']);});
test('source directory search supports combined name, region, kind and coverage filters',()=>{const sources=[{id:'a',name:'Aurora Lab',kind:'Colorizer',region:'Europe',tracking:'directory'},{id:'b',name:'Aurora Mint',kind:'Private mint',region:'Europe',tracking:'automatic'},{id:'c',name:'Aurora Color',kind:'Colorizer',region:'Asia',tracking:'directory'}];assert.deepEqual(filterSources(sources,{query:'aurora europe',kind:'Colorizer',tracking:'directory'}).map(s=>s.id),['a']);assert.deepEqual(filterSources(sources,{region:'Europe',tracking:'automatic'}).map(s=>s.id),['b']);assert.equal(filterSources(sources,{query:'missing'}).length,0);});

test('sources moved to the directory remain selectable for archived listings',()=>{const sources=[{id:'a',name:'Active',tracking:'automatic',status:'ok'},{id:'b',name:'Archived',tracking:'directory',status:'directory'},{id:'c',name:'Discovery only',tracking:'directory',status:'directory'}];const retained=[{id:'one',sourceId:'a'},{id:'two',sourceId:'b'}];assert.deepEqual(listingSources(sources,retained).map(s=>s.id),['a','b']);assert.deepEqual(filterProducts(retained,{source:'b'}).map(p=>p.id),['two']);assert.equal(sourceCoverage(sources).automatic.length,1);});

const colorListings=[
 {id:'silver',title:'Colorized Silver Eagle',metal:'Silver',category:'Coin',status:'presale',mintage:199},
 {id:'gold',title:'Colorized gold coin',metal:'Gold',category:'Coin'},
 {id:'copper',title:'Colorized copper coin',metal:'Copper',category:'Coin'},
 {id:'art',title:'Silver art coin',metal:'Silver',category:'Coin',description:'A sculptural tribute.'},
 {id:'bar',title:'Enamel silver bar',metal:'Silver',category:'Bar'},
 {id:'late-body',title:'Moonlight 1oz',description:'Shortened product introduction.',metal:'Silver',category:'Coin',isColorized:true,colorizedSilver:true,isColorizedSilverCoin:true,colorizedEvidence:'Description: vivid colour printing on the reverse'},
 {id:'negated',title:'Non-colorized silver coin',metal:'Silver',category:'Coin'},
 {id:'unconfirmed',title:'Colorized design',metal:'Silver',category:'Coin',isColorized:false,colorizedSilver:false,isColorizedSilverCoin:false}
];
test('strict colorized silver focus excludes other metals, bars and art-only language',()=>{
 assert.deepEqual(filterProducts(colorListings,{focus:'colorized-silver-coins'}).map(p=>p.id),['silver','late-body']);
 assert.deepEqual(filterProducts(colorListings,{lens:'color'}).map(p=>p.id),['silver','late-body']);
 assert.equal(filterProducts([{...colorListings[5],metal:'Other'}],{focus:'colorized-silver-coins'}).length,0);
});
test('full-metadata classifier flags retain color evidence beyond the displayed description',()=>{
 const p=colorListings[5];assert.equal(colorEvidence(p),'Description: vivid colour printing on the reverse');
 assert.equal(filterProducts([p],{focus:'colorized-silver-coins'}).length,1);
 assert.equal(filterProducts([{...p,category:'Collectible'}],{focus:'colorized-silver-coins'}).length,1,'the full classifier can recover a coin category absent from the short title');
 assert.equal(filterProducts([{...p,category:'Collectible'}],{focus:'colorized-silver-coins',category:'Coin'}).length,1,'the Coins tab retains recovered coin evidence in strict focus');
 assert.equal(colorEvidence(colorListings[7]),null);
});
test('legacy fallback requires positive listing color words and ignores source names',()=>{
 const listings=[{id:'body',title:'Silver coin',metal:'Silver',category:'Coin',description:'The reverse is colourized.'},{id:'tag',title:'Silver coin',metal:'Silver',category:'Coin',tags:['Colorized']},{id:'source',title:'Silver art coin',metal:'Silver',category:'Coin',sourceName:'Colorized Coin Store'},{id:'no',title:'Silver coin',metal:'Silver',category:'Coin',description:'This is not colorized.'}];
 assert.deepEqual(filterProducts(listings,{focus:'colorized-silver-coins'}).map(p=>p.id),['body','tag']);
 for(const description of ['Presented in a colored box.','Includes a digitally printed certificate.','A digital printed certificate accompanies the coin.','The box is colorized.','Chromatics series'])assert.equal(colorEvidence({description}),null,description);
 assert.notEqual(colorEvidence({description:'Colorized silver coin in a wooden box.'}),null);
});
test('bars and collectibles broaden strict focus while preserving the colorized silver requirement',()=>{
 const focus=focusForCategory('colorized-silver-coins','Bar');assert.equal(focus,'colorized-silver');
 assert.deepEqual(filterProducts(colorListings,{focus,category:'Bar'}).map(p=>p.id),['bar']);
 assert.equal(focusForCategory('colorized-silver-coins','Collectible'),'colorized-silver');
 assert.equal(focusForCategory('all','Bar'),'all');
 assert.equal(focusForCategory('colorized-silver-coins','Coin'),'colorized-silver-coins');
});
test('all releases bypasses focus and other lenses compose with focused listings',()=>{
 assert.equal(filterProducts(colorListings,{focus:'all'}).length,colorListings.length);
 assert.deepEqual(filterProducts(colorListings,{focus:'colorized-silver-coins',lens:'presale'}).map(p=>p.id),['silver']);
 assert.deepEqual(filterProducts(colorListings,{focus:'colorized-silver-coins',lens:'limited'}).map(p=>p.id),['silver']);
 assert.deepEqual(filterProducts(colorListings,{focus:'colorized-silver',view:'watchlist'},new Set(['bar','gold'])).map(p=>p.id),['bar']);
});
