'use strict';
const box = document.querySelector('#items');
let items = [];
function el(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; }
// Word-level LCS marks changed words on both sides, preserving punctuation/spacing.
function diff(left, right) {
  const split = s => s.match(/\s+|[^\s]+/gu) || [];
  const a = split(left), b = split(right);
  const aa = a.filter(s => !/^\s+$/u.test(s)), bb = b.filter(s => !/^\s+$/u.test(s));
  // Avoid quadratic work for unusually long dictations: mark the whole changed text.
  if (aa.length * bb.length > 1000000) return [el('mark', left), el('mark', right)];
  const dp = Array.from({length: aa.length + 1}, () => new Uint16Array(bb.length + 1));
  for (let i=aa.length-1;i>=0;i--) for(let j=bb.length-1;j>=0;j--) dp[i][j]=aa[i]===bb[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1]);
  const keepA = new Set(), keepB = new Set(); let i=0,j=0;
  while(i<aa.length && j<bb.length) { if(aa[i]===bb[j]) {keepA.add(i++);keepB.add(j++);} else if(dp[i+1][j]>=dp[i][j+1]) i++; else j++; }
  return [[a,keepA],[b,keepB]].map(([parts,keep]) => { const span=el('span');let n=0;for(const s of parts) span.append(/^\s+$/u.test(s)||keep.has(n++)?document.createTextNode(s):el('mark',s));return span; });
}
async function start() {
  items = (await (await fetch('items')).json()).items;
  items.forEach((item,index) => {
    const card=el('article');card.id=`item-${index}`;
    card.append(el('h2',`${index+1} of ${items.length}`));
    const audio=el('audio');audio.controls=true;audio.preload='none';audio.src=`audio/${index}`;card.append(audio);
    const inserted=item.cleaned_text||item.text;
    const [a,b]=diff(inserted,item.recheck);
    card.append(el('h3','Inserted text'));const pa=el('p');pa.append(a);card.append(pa);
    if(item.cleaned_text && item.cleaned_text!==item.text) card.append(el('h3','Original Whisper text'),el('p',item.text));
    card.append(el('h3','Re-check'));const pb=el('p');pb.append(b);card.append(pb);
    for(const [value,title] of [['inserted','Inserted was right'],['recheck','Re-check was right'],['typed','Type the correct text'],['skip','Skip']]) {
      const label=el('label'),input=el('input');input.type='radio';input.name=`choice-${index}`;input.value=value;input.checked=value==='skip';label.append(input,document.createTextNode(title));card.append(label);
    }
    const field=el('textarea');field.value=item.recheck;field.setAttribute('aria-label','Correct text');field.addEventListener('input',()=>{card.querySelector('[value="typed"]').checked=true;});card.append(field);box.append(card);
  });
}
document.querySelector('#done').addEventListener('click',async()=>{
  const button=document.querySelector('#done'),result=document.querySelector('#result');button.disabled=true;
  const answers=items.map((item,i)=>({id:item.id,choice:document.querySelector(`#item-${i} input:checked`).value,text:document.querySelector(`#item-${i} textarea`).value}));
  try {
    const response=await fetch('save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(answers)});
    if(!response.ok) throw new Error();const saved=await response.json();
    result.textContent=`Saved. ${saved.instruction} Finder shows the import file. ${saved.copied?'Corrections copied to the node.':'Node copy pending; corrections are safe locally. Run the retry command in the README.'} The review server has closed; you can close this tab.`;
  } catch {result.textContent='Could not save. The server may have timed out. Reopen the review command and try again.';button.disabled=false;}
});
start().catch(()=>{document.querySelector('#result').textContent='Review unavailable. Reopen the review command.';document.querySelector('#done').disabled=true;});
let lastActivity=0;
function activity() {if(Date.now()-lastActivity>15000) {lastActivity=Date.now();fetch('activity',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});}}
for(const event of ['pointerdown','keydown','input']) document.addEventListener(event,activity,{passive:true});
window.addEventListener('pagehide',()=>navigator.sendBeacon('close',new Blob(['{}'],{type:'application/json'})));
