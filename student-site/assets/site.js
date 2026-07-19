const $ = id => document.getElementById(id);

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
      const keep=(node.tagName==="A"&&["href","title"].includes(attr.name))||(node.tagName==="IMG"&&["src","alt","title"].includes(attr.name))||["data-math","data-display"].includes(attr.name);
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

async function renderCatalog(){
  const data=await catalog(); const questions=Array.isArray(data.questions)?data.questions:[]; const list=$("question-list"), summary=$("catalog-summary"), empty=$("catalog-empty"), search=$("question-search");
  function draw(){
    const query=search.value.trim().toLowerCase(); const visible=questions.filter(item=>[item.title,item.subject,...(item.knowledge_points||[])].join(" ").toLowerCase().includes(query));
    list.replaceChildren(); summary.textContent=query?`找到 ${visible.length} 道题`:`共 ${questions.length} 道已复核题目`; empty.classList.toggle("hidden",visible.length>0);
    for(const item of visible){
      const card=document.createElement("a"); card.className="question-card"; card.href=`viewer.html?id=${encodeURIComponent(item.id)}`;
      const subject=document.createElement("small"); subject.textContent=item.subject||"高中物理";
      const title=document.createElement("h2"); title.textContent=item.title;
      const tags=document.createElement("div"); tags.className="tag-list"; for(const point of item.knowledge_points||[])tags.append(tag(point));
      const open=document.createElement("span"); open.className="open-label"; open.textContent=item.simulation?"阅读解析 · 含交互演示 →":"阅读解析 →";
      card.append(subject,title,tags,open); list.append(card);
    }
  }
  search.addEventListener("input",draw); draw();
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

installMath();
const task=document.body.dataset.page==="viewer"?renderViewer():renderCatalog();
task.catch(error=>{ const target=$("markdown-content")||$("catalog-summary"); if(target)target.textContent=`加载失败：${error.message}`; });
