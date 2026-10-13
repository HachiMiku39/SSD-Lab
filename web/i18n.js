/* Local catalogs only: no online translation or locale-dependent benchmark data. */
window.I18n = (() => {
  const languages=["en","zh-CN","zh-TW","ja","ko","de","fr","es"];
  const catalog=new Map(), aliases=new Map(), patterns=[];
  const escapeRE=s=>s.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
  for(const row of window.LocaleRows) {
    if(row.length!==8) throw new Error("Invalid localization row: "+row[0]);
    const values=[row[1],row[0],...row.slice(2)]; catalog.set(row[0],values);
    if(row[0].includes("{")) {
      for(const template of new Set(values)) {
        const names=[];let last=0,expression="";
        for(const match of template.matchAll(/\{(\w+)\}/g)){expression+=escapeRE(template.slice(last,match.index))+"(.+?)";names.push(match[1]);last=match.index+match[0].length;}
        expression+=escapeRE(template.slice(last)); patterns.push({key:row[0],names,re:new RegExp("^"+expression+"$")});
      }
    } else for(const value of values) if(!aliases.has(value)) aliases.set(value,row[0]);
  }
  let locale="en";try {const saved=window.SSDNativePreferences?.language||localStorage.getItem("ssd-language");if(languages.includes(saved))locale=saved;}catch(_){}
  const sourceNodes=new WeakMap(),sourceAttributes=new WeakMap();
  function text(source,params={}) {
    if(source==="{n} 轮" && Number(params.n)===1)return text("1 轮");
    const key=catalog.has(source)?source:aliases.get(source);
    if(key) return catalog.get(key)[languages.indexOf(locale)].replace(/\{(\w+)\}/g,(_,name)=>params[name]??"{"+name+"}");
    for(const pattern of patterns){const match=String(source).match(pattern.re);if(match) return text(pattern.key,Object.fromEntries(pattern.names.map((name,index)=>[name,match[index+1]])));}
    return source;
  }
  // Native reports, paths and device identifiers are evidence and are never translated.
  const excluded="script,style,pre,code,#smart-identity dd,input,[data-verbatim]";
  function translateNode(node) {
    if(node.nodeType!==Node.TEXT_NODE || !node.parentElement || node.parentElement.closest(excluded))return;
    const old=sourceNodes.get(node),value=node.nodeValue;
    const source=old && value===old.rendered?old.source:value;
    const trimmed=source.trim();if(!trimmed)return;
    const translated=text(trimmed),rendered=source.replace(trimmed,translated);
    sourceNodes.set(node,{source,rendered});if(value!==rendered)node.nodeValue=rendered;
  }
  function translateAttributes(element) {
    let values=sourceAttributes.get(element);if(!values){values={};sourceAttributes.set(element,values);}
    for(const name of ["placeholder","title","aria-label"]){const value=element.getAttribute(name);if(!value)continue;const old=values[name];const source=old&&value===old.rendered?old.source:value;const rendered=text(source);values[name]={source,rendered};if(value!==rendered)element.setAttribute(name,rendered);}
  }
  function translate(root=document.body) {
    if(!root)return;if(root.nodeType===Node.TEXT_NODE){translateNode(root);return;}
    if(root.nodeType===Node.ELEMENT_NODE)translateAttributes(root);
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode()))translateNode(node);
    root.querySelectorAll?.("[placeholder],[title],[aria-label]").forEach(translateAttributes);
  }
  const menuSources={about:"关于 SSD Lab",quit:"退出 SSD Lab",edit:"编辑",undo:"撤销",cut:"剪切",copy:"复制",paste:"粘贴",all:"全选",close:"关闭",choose:"选择测试目录",export:"导出报告",aboutDescription:"本地 SSD 性能与健康工作台",affiliation:"独立项目，与 Crystal Dew World 无隶属关系。"};
  function notifyNative(){window.webkit?.messageHandlers?.appearance?.postMessage({theme:window.Appearance.theme,language:locale,...Object.fromEntries(Object.entries(menuSources).map(([key,value])=>[key,text(value)]))});}
  function setLocale(value){
    if(!languages.includes(value))return;locale=value;document.documentElement.lang=value;
    try{localStorage.setItem("ssd-language",value);}catch(_){}
    document.getElementById("language-select").value=value;
    translate(document);notifyNative();window.dispatchEvent(new Event("languagechange"));
  }
  const observer=new MutationObserver(records=>{
    for(const record of records){if(record.type==="characterData")translateNode(record.target);else if(record.type==="attributes")translateAttributes(record.target);else record.addedNodes.forEach(translate);}
  });
  translate(document);document.documentElement.lang=locale;
  document.getElementById("language-select").value=locale;
  document.getElementById("language-select").addEventListener("change",event=>setLocale(event.target.value));
  observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:["placeholder","title","aria-label"]});
  document.addEventListener("DOMContentLoaded",notifyNative);
  return {text,translate,setLocale,get locale(){return locale;},languages,catalog};
})();
const t=(source,params)=>window.I18n.text(source,params);
