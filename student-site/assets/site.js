const $ = id => document.getElementById(id);

function installTheme(){
  const root=document.documentElement, toggle=$("theme-toggle"), media=matchMedia("(prefers-color-scheme: dark)");
  let saved; try{saved=localStorage.getItem("wuli-theme");}catch{}
  const apply=theme=>{
    root.dataset.theme=theme; root.style.colorScheme=theme;
    if(toggle){const next=theme==="dark"?"light":"dark";toggle.setAttribute("aria-label",`切换为${next==="dark"?"暗色":"亮色"}模式`);const label=toggle.querySelector(".theme-toggle-label");if(label)label.textContent=next==="dark"?"暗色模式":"亮色模式";}
  };
  apply(saved==="light"||saved==="dark"?saved:(media.matches?"dark":"light"));
  toggle?.addEventListener("click",()=>{const next=root.dataset.theme==="dark"?"light":"dark";try{localStorage.setItem("wuli-theme",next);}catch{}apply(next);});
  media.addEventListener?.("change",event=>{let preference;try{preference=localStorage.getItem("wuli-theme");}catch{}if(!preference)apply(event.matches?"dark":"light");});
}

function installMath() {
  if (!window.marked) return;
  window.marked.use({ extensions: [
    { name:"displayMath", level:"block", start:src=>src.indexOf("$$"), tokenizer(src){ const m=/^\$\$\s*([\s\S]+?)\s*\$\$(?:\n|$)/.exec(src); if(m)return{type:"displayMath",raw:m[0],text:m[1]}; }, renderer:t=>`<div data-math="${encodeURIComponent(t.text)}" data-display="1"></div>` },
    { name:"inlineMath", level:"inline", start:src=>src.indexOf("$"), tokenizer(src){ const m=/^\$([^$\n]+?)\$/.exec(src); if(m)return{type:"inlineMath",raw:m[0],text:m[1]}; }, renderer:t=>`<span data-math="${encodeURIComponent(t.text)}" data-display="0"></span>` },
  ]});
}

function safeFragment(html, contentUrl) {
  const template=document.createElement("template"); template.innerHTML=html;
  const tags=new Set(["A","BLOCKQUOTE","BR","CODE","DEL","DIV","EM","H1","H2","H3","H4","H5","H6","HR","IMG","LI","OL","P","PRE","SPAN","STRONG","SUB","SUP","TABLE","TBODY","TD","TH","THEAD","TR","UL"]);
  for(const node of [...template.content.querySelectorAll("*")]){
    if(!tags.has(node.tagName)){ node.remove(); continue; }
    for(const attr of [...node.attributes]){
      const keep=(node.tagName==="A"&&["href","title"].includes(attr.name))||(node.tagName==="IMG"&&["src","alt","title"].includes(attr.name))||["data-math","data-display","data-note"].includes(attr.name)||["class","style"].includes(attr.name);
      if(!keep)node.removeAttribute(attr.name);
    }
    if(node.tagName==="A"){
      const href=node.getAttribute("href")||"";
      if(/^(https?:|mailto:|#)/i.test(href)){ node.target="_blank"; node.rel="noopener noreferrer"; }
      else { try{ node.href=new URL(href,contentUrl).href; }catch{ node.removeAttribute("href"); } }
    }
    if(node.tagName==="IMG"){
      const src=node.getAttribute("src")||"";
      if(/^(data:|javascript:|https?:)/i.test(src))node.remove();
      else try{ node.src=new URL(src,contentUrl).href; }catch{ node.remove(); }
    }
  }
  return template.content;
}

async function catalog(){ const response=await fetch("catalog.json",{cache:"no-store"}); if(!response.ok)throw new Error("题库清单无法读取"); return response.json(); }
function tag(text){ const span=document.createElement("span"); span.className="tag"; span.textContent=text; return span; }
function catalogTime(item){ const raw=item.uploaded_at||item.published_at||""; const value=Date.parse(raw); return Number.isFinite(value)?value:null; }
function catalogDifficulty(item){ const value=Number(item.difficulty?.score); return Number.isFinite(value)?value:null; }
function uploadTimeLabel(item){
  const value=catalogTime(item); if(value===null)return "";
  const prefix=item.uploaded_at?"上传":"发布";
  return `${prefix} ${new Intl.DateTimeFormat("zh-CN",{year:"numeric",month:"short",day:"numeric"}).format(value)}`;
}
const DIFFICULTY_COLORS={"基础":"#34D399","较易":"#A3E635","中等":"#FBBF24","较难":"#FB923C","挑战":"#F43F5E"};
const RADAR_LABELS={"knowledge_depth":"知识深度","knowledge_integration":"知识交叉","type_distance":"题型距离","structure":"结构复杂","calculation_expression":"运算表达","condition_discernment":"条件辨析"};
const RADAR_LABEL_ORDER=["知识深度","知识交叉","题型距离","结构复杂","运算表达","条件辨析"];
let activeDifficultyPopover=null;
function isDifficultyPopoverOpen(panel){try{return panel.matches(":popover-open");}catch{return panel.classList.contains("is-open");}}
function closeDifficultyPopover(target=activeDifficultyPopover){
  if(!target)return;
  const {dot,panel}=target; if(typeof panel.hidePopover==="function"&&isDifficultyPopoverOpen(panel))panel.hidePopover();else panel.classList.remove("is-open");
  dot.classList.remove("expanded");dot.setAttribute("aria-expanded","false");if(activeDifficultyPopover?.panel===panel)activeDifficultyPopover=null;
}
function positionDifficultyPopover(dot,panel){
  if(matchMedia("(max-width:760px)").matches){panel.style.left="21px";panel.style.right="21px";panel.style.top="auto";panel.style.bottom="16px";panel.style.width="auto";return;}
  panel.style.right="auto";panel.style.bottom="auto";panel.style.width="min(330px, calc(100vw - 42px))";
  const anchor=dot.getBoundingClientRect(), box=panel.getBoundingClientRect(), margin=12;
  const left=Math.max(margin,Math.min(anchor.left-8,innerWidth-box.width-margin));
  const below=anchor.bottom+10, top=below+box.height<=innerHeight-margin?below:Math.max(margin,anchor.top-box.height-10);
  panel.style.left=`${left}px`;panel.style.top=`${top}px`;
}
function openDifficultyPopover(dot,panel){
  if(activeDifficultyPopover?.panel!==panel)closeDifficultyPopover();
  activeDifficultyPopover={dot,panel};panel.style.setProperty("--difficulty-color",dot.style.getPropertyValue("--difficulty-color"));
  if(typeof panel.showPopover==="function"){if(!isDifficultyPopoverOpen(panel))panel.showPopover();}else panel.classList.add("is-open");
  dot.classList.add("expanded");dot.setAttribute("aria-expanded","true");requestAnimationFrame(()=>positionDifficultyPopover(dot,panel));
}
function radarSvg(dimensions,score,level){
  const ns="http://www.w3.org/2000/svg", centerX=130, centerY=108, radius=64, labelRadius=91, count=6;
  const normalized=[...Array(count)].map((_,index)=>dimensions[index]||{});
  const point=(index,scale=1,distance=radius)=>{const angle=-Math.PI/2+index*Math.PI*2/count; return [centerX+Math.cos(angle)*distance*scale,centerY+Math.sin(angle)*distance*scale];};
  const svg=document.createElementNS(ns,"svg"); svg.setAttribute("viewBox","0 0 260 220"); svg.setAttribute("role","img"); svg.setAttribute("aria-label",`六维难度图，${level}，总分 ${score}`); svg.classList.add("difficulty-radar");
  for(const scale of [.25,.5,.75,1]){const poly=document.createElementNS(ns,"polygon"); poly.setAttribute("points",[...Array(count)].map((_,i)=>point(i,scale).join(",")).join(" ")); poly.setAttribute("class","radar-grid"); svg.append(poly);}
  normalized.forEach((item,index)=>{
    const end=point(index), line=document.createElementNS(ns,"line"); line.setAttribute("x1",centerX);line.setAttribute("y1",centerY);line.setAttribute("x2",end[0]);line.setAttribute("y2",end[1]);line.setAttribute("class","radar-axis");svg.append(line);
    const [labelX,labelY]=point(index,1,labelRadius), label=document.createElementNS(ns,"text"); const angle=-Math.PI/2+index*Math.PI*2/count;
    label.setAttribute("x",labelX);label.setAttribute("y",labelY);label.setAttribute("text-anchor",Math.cos(angle)>.3?"start":Math.cos(angle)<-.3?"end":"middle");label.setAttribute("dominant-baseline","middle");label.setAttribute("class","radar-label");
    const name=document.createElementNS(ns,"tspan"); name.setAttribute("x",labelX); name.textContent=RADAR_LABELS[item.id]||RADAR_LABEL_ORDER[index]; label.append(name);
    const value=document.createElementNS(ns,"tspan"); value.setAttribute("x",labelX); value.setAttribute("dy","13"); value.setAttribute("class","radar-label-value"); value.textContent=`${Number(item.score)||0}/5`; label.append(value); svg.append(label);
  });
  const area=document.createElementNS(ns,"polygon"); area.setAttribute("points",normalized.map((item,index)=>point(index,Math.max(0,Math.min(5,Number(item.score)||0))/5).join(",")).join(" ")); area.setAttribute("class","radar-area");svg.append(area);
  normalized.forEach((item,index)=>{const [x,y]=point(index,Math.max(0,Math.min(5,Number(item.score)||0))/5), node=document.createElementNS(ns,"circle");node.setAttribute("cx",x);node.setAttribute("cy",y);node.setAttribute("r","2.8");node.setAttribute("class","radar-node");svg.append(node);});
  const total=document.createElementNS(ns,"text"); total.setAttribute("x",centerX);total.setAttribute("y",centerY-2);total.setAttribute("text-anchor","middle");total.setAttribute("class","radar-total");total.textContent=String(score);svg.append(total);
  const totalLabel=document.createElementNS(ns,"text");totalLabel.setAttribute("x",centerX);totalLabel.setAttribute("y",centerY+12);totalLabel.setAttribute("text-anchor","middle");totalLabel.setAttribute("class","radar-total-label");totalLabel.textContent=level;svg.append(totalLabel);
  return svg;
}
function difficultyIndicator(difficulty){
  if(!difficulty||!Number.isFinite(Number(difficulty.score))||!difficulty.level)return null;
  const dot=document.createElement("span"); dot.className="difficulty-dot"; dot.tabIndex=0; dot.style.setProperty("--difficulty-color",DIFFICULTY_COLORS[difficulty.level]||"#94a3b8"); dot.setAttribute("aria-label",`客观难度 ${difficulty.score}/100，${difficulty.level}`);dot.setAttribute("aria-expanded","false");
  const iconNs="http://www.w3.org/2000/svg", glyph=document.createElementNS(iconNs,"svg");glyph.setAttribute("viewBox","0 0 24 24");glyph.setAttribute("aria-hidden","true");glyph.classList.add("difficulty-glyph");
  const frame=document.createElementNS(iconNs,"polygon");frame.setAttribute("points","12,3 19.8,7.5 19.8,16.5 12,21 4.2,16.5 4.2,7.5");frame.setAttribute("class","difficulty-glyph-frame");glyph.append(frame);
  [[12,4.8,12,19.2],[5.8,8.4,18.2,15.6],[18.2,8.4,5.8,15.6]].forEach(values=>{const axis=document.createElementNS(iconNs,"line");["x1","y1","x2","y2"].forEach((name,index)=>axis.setAttribute(name,values[index]));axis.setAttribute("class","difficulty-glyph-axis");glyph.append(axis);});
  const core=document.createElementNS(iconNs,"circle");core.setAttribute("cx","12");core.setAttribute("cy","12");core.setAttribute("r","2.2");core.setAttribute("class","difficulty-glyph-core");glyph.append(core);dot.append(glyph);
  const panel=document.createElement("span"); panel.className="difficulty-popover"; panel.setAttribute("role","tooltip");panel.setAttribute("popover","manual");
  const dims=Array.isArray(difficulty.dimensions)?difficulty.dimensions:[]; panel.append(radarSvg(dims,difficulty.score,difficulty.level));
  if(difficulty.summary){const conclusion=document.createElement("span"); conclusion.className="difficulty-conclusion"; const label=document.createElement("small");label.textContent="难度结论";const summary=document.createElement("span");summary.textContent=difficulty.summary;conclusion.append(label,summary);panel.append(conclusion);}
  document.body.append(panel);
  let closeTimer;
  const cancelClose=()=>clearTimeout(closeTimer), scheduleClose=()=>{closeTimer=setTimeout(()=>closeDifficultyPopover({dot,panel}),100);};
  dot.addEventListener("mouseenter",()=>{if(!matchMedia("(max-width:760px)").matches){cancelClose();openDifficultyPopover(dot,panel);}});dot.addEventListener("mouseleave",scheduleClose);
  panel.addEventListener("mouseenter",cancelClose);panel.addEventListener("mouseleave",scheduleClose);
  dot.addEventListener("focus",()=>{if(!matchMedia("(max-width:760px)").matches)openDifficultyPopover(dot,panel);});dot.addEventListener("blur",scheduleClose);
  dot.addEventListener("click",event=>{if(matchMedia("(max-width:760px)").matches){event.preventDefault();event.stopPropagation();isDifficultyPopoverOpen(panel)?closeDifficultyPopover({dot,panel}):openDifficultyPopover(dot,panel);}});
  return dot;
}

async function renderCatalog(){
  const data=await catalog(); const questions=Array.isArray(data.questions)?data.questions:[]; const list=$("question-list"), summary=$("catalog-summary"), empty=$("catalog-empty"), search=$("question-search"), sort=$("question-sort");
  function draw(){
    const query=search.value.trim().toLowerCase(), difficultySort=sort.value.startsWith("difficulty"), direction=(sort.value==="uploaded-asc"||sort.value==="difficulty-asc")?1:-1;
    const visible=questions
      .map((item,index)=>({item,index,time:catalogTime(item),difficulty:catalogDifficulty(item)}))
      .filter(({item})=>[item.title,item.subject,...(item.knowledge_points||[])].join(" ").toLowerCase().includes(query))
      .sort((left,right)=>{
        const leftValue=difficultySort?left.difficulty:left.time, rightValue=difficultySort?right.difficulty:right.time;
        if(leftValue===null&&rightValue!==null)return 1;
        if(leftValue!==null&&rightValue===null)return -1;
        return ((leftValue||0)-(rightValue||0))*direction||left.index-right.index;
      });
    closeDifficultyPopover();document.querySelectorAll(".difficulty-popover").forEach(panel=>panel.remove());
    list.replaceChildren(); summary.textContent=query?`找到 ${visible.length} 道题`:`共 ${questions.length} 道已复核题目`; empty.classList.toggle("hidden",visible.length>0);
    for(const {item} of visible){
      const card=document.createElement("a"); card.className="question-card"; card.href=`viewer.html?id=${encodeURIComponent(item.id)}`;
      const meta=document.createElement("div"); meta.className="question-card-meta";
      const subject=document.createElement("small"); subject.textContent=item.subject||"高中物理"; meta.append(subject);
      const indicator=difficultyIndicator(item.difficulty); if(indicator) meta.append(indicator);
      const timeText=uploadTimeLabel(item); if(timeText){ const time=document.createElement("time"); time.className="upload-time"; time.dateTime=item.uploaded_at||item.published_at; time.textContent=timeText; meta.append(time); }
      const title=document.createElement("h2"); title.textContent=item.title;
      const tags=document.createElement("div"); tags.className="tag-list"; for(const point of item.knowledge_points||[])tags.append(tag(point));
      const open=document.createElement("span"); open.className="open-label"; open.textContent=item.simulation?"阅读解析 · 含交互演示 →":"阅读解析 →";
      card.append(meta,title,tags,open); list.append(card);
    }
  }
  search.addEventListener("input",draw); sort.addEventListener("change",draw); draw();
}

async function renderViewer(){
  const data=await catalog(); const id=new URLSearchParams(location.search).get("id"); const item=(data.questions||[]).find(question=>question.id===id);
  if(!item)throw new Error("这道题不存在或尚未发布");
  document.title=`${item.title} · 悟理学习站`; $("reader-title").textContent=item.title; const tags=$("reader-tags"); for(const point of item.knowledge_points||[])tags.append(tag(point));
  if(item.pdf){ const link=$("pdf-link"); link.href=item.pdf; link.classList.remove("hidden"); }
  if(item.simulation){ const link=$("simulation-link"); link.href=item.simulation; link.classList.remove("hidden"); }
  const contentUrl=new URL(item.content,location.href); const response=await fetch(contentUrl,{cache:"no-store"}); if(!response.ok)throw new Error("题目内容无法读取"); const markdown=await response.text();
  const target=$("markdown-content"); target.replaceChildren(safeFragment(window.marked.parse(markdown,{gfm:true,breaks:true}),contentUrl));
  for(const math of target.querySelectorAll("[data-math]")){ window.katex.render(decodeURIComponent(math.dataset.math||""),math,{displayMode:math.dataset.display==="1",throwOnError:false,strict:"ignore",trust:false,output:"htmlAndMathml"}); }
}

installTheme();
installMath();
const task=document.body.dataset.page==="viewer"?renderViewer():renderCatalog();
task.catch(error=>{ const target=$("markdown-content")||$("catalog-summary"); if(target)target.textContent=`加载失败：${error.message}`; });
