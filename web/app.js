"use strict";
const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const profiles = {
  standard: [{id:"seq8",label:"SEQ1M",detail:"Q8T1"},{id:"seq1",label:"SEQ1M",detail:"Q1T1"},{id:"rnd32",label:"RND4K",detail:"Q32T1"},{id:"rnd1",label:"RND4K",detail:"Q1T1"}],
  nvme: [{id:"seq8",label:"SEQ1M",detail:"Q8T1"},{id:"seq32",label:"SEQ128K",detail:"Q32T1"},{id:"rnd16",label:"RND4K",detail:"Q32T16"},{id:"rnd1",label:"RND4K",detail:"Q1T1"}],
  qos: [1,4,16,32].map(depth=>({id:"mix"+depth,label:"MIX4K",detail:"R70/W30 Q"+depth+"T1"}))
};
profiles.posix=[{id:"seq1",label:"SEQ1M",detail:"Q1T1"},{id:"seq128",label:"SEQ128K",detail:"Q1T1"},{id:"rnd1",label:"RND4K",detail:"Q1T1"},{id:"rnd4t",label:"RND4K",detail:"Q1T4"}];
$("profile").insertAdjacentHTML("beforeend",'<option value="posix">POSIX 同步 · Q1</option>');
const names = {benchmark:t("性能测试"),plots:t("散点分析"),smart:t("磁盘健康"),history:t("测试记录"),methodology:t("测试方法"),settings:t("工具设置")};
const queryToken = new URLSearchParams(location.search).get("token");
if (queryToken) {sessionStorage.setItem("ssd-token", queryToken); history.replaceState(null,"",location.pathname+location.hash);}
const token = sessionStorage.getItem("ssd-token") || "";
let currentUnit = "mb_s", currentState = {}, currentSmart = null, records = [], selectedHistoryId = null, lastResultsKey = "", pollBusy = false, disconnected = false;
let toastTimer, pathTimer;
const units = {mb_s:"MB/s",iops:"IOPS",latency_us:"μs"};

async function api(path, data) {
  const response = await fetch("/api/"+path, {method:data === undefined?"GET":"POST",headers:{"X-SSD-Token":token,...(data===undefined?{}:{"Content-Type":"application/json"})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || t("请求失败"));
  return result;
}
function toast(message) {clearTimeout(toastTimer); $("toast").textContent=message; $("toast").classList.remove("hidden"); toastTimer=setTimeout(()=>$("toast").classList.add("hidden"),5500);}
async function guarded(button, action) {
  const previous = button.disabled; button.disabled=true;
  try {await action();} catch(error) {toast(t(error.message));} finally {button.disabled=previous;}
}
function bytes(value) {if (value===null || value===undefined) return "—"; if (value===0) return "0 B"; const k=Math.min(4,Math.floor(Math.log(Math.max(value,1))/Math.log(1024))); return (value/1024**k).toFixed(k>=3?2:0)+" "+["B","KiB","MiB","GiB","TiB"][k];}
function numeric(value,decimals=0) {return value===null || value===undefined?"—":Number(value).toLocaleString(I18n.locale,{maximumFractionDigits:decimals});}
function metric(value) {if(value===null || value===undefined) return "—"; return currentUnit==="iops"?Math.round(value).toLocaleString(I18n.locale):Number(value).toFixed(value>=10000?0:2);}
function date(value) {return new Date(value).toLocaleString(I18n.locale,{hour12:false});}
function runConfig(row="all") {return {path:$("target-path").value.trim(),profile:$("profile").value,size_mib:Number($("size").value),duration:Number($("duration").value),passes:Number($("passes").value),direct:$("direct").checked,engine:$("engine").value,plot_mode:$("plot-mode").value,row};}
function matchingConfig() {const current=runConfig(), old=currentState.config; return old && Object.keys(current).every(key=>key==="row" || current[key]===old[key]);}
function storePreferences() {try {localStorage.setItem("ssd-preferences",JSON.stringify(runConfig()));} catch(_) {} }
function loadPreferences() {try {const value=JSON.parse(localStorage.getItem("ssd-preferences")||"null"); if(!value) return; $("target-path").value=value.path||""; for(const [id,key] of [["profile","profile"],["size","size_mib"],["duration","duration"],["passes","passes"]]) {if([...$(id).options].some(option=>option.value===String(value[key]))) $(id).value=String(value[key]);} for(const [id,key] of [["engine","engine"],["plot-mode","plot_mode"]]) {if([...$(id).options].some(option=>option.value===String(value[key]))) $(id).value=String(value[key]);} if(typeof value.direct==="boolean") $("direct").checked=value.direct;} catch(_) {} }

async function navigate(page) {
  if(!names[page]) page="benchmark";
  document.querySelectorAll(".page").forEach(element=>element.classList.toggle("active",element.id==="page-"+page));
  document.querySelectorAll(".nav-item").forEach(element=>element.classList.toggle("active",element.dataset.page===page));
  $("current-page-name").textContent=t(names[page]);
  if(location.hash!=="#"+page) history.replaceState(null,"","#"+page);
  if(page==="history") {try {await loadHistory();} catch(error) {toast(t(error.message));}}
  if(page==="plots" && window.Scatter) await window.Scatter.load(currentState.plot_id||currentState.record_id);
}
function updateEstimate() {
  const seconds=($("profile").value==="qos"?4:8)*(Number($("duration").value)+($("plot-mode").value==="per_io"?0:1))*Number($("passes").value);
  const time=seconds>=60?t("{n} 分钟",{n:(seconds/60).toFixed(1)}):t("{n} 秒",{n:seconds});
  $("estimated-time").textContent=t("约 {n} + 文件准备",{n:time});
  $("cache-label").textContent=$("direct").checked?"Direct I/O":t("系统缓存参与");storePreferences();renderResults(true);
}
function renderResults(force=false) {
  const matches=matchingConfig(), results=matches?(currentState.results||{}):{}, active=matches?currentState.current:null;
  const key=JSON.stringify([$("profile").value,results,currentUnit,active,currentState.running,I18n.locale]);
  if(!force && key===lastResultsKey) return; lastResultsKey=key;
  const rows=profiles[$("profile").value];
  const maximum=Math.max(1,...Object.values(results).flatMap(row=>[row.read?.[currentUnit]||0,row.write?.[currentUnit]||0]));
  $("result-rows").innerHTML=rows.map(row=>`<div class="result-row"><button class="row-title" data-row="${row.id}" title="${escapeHTML(t("测试"))} ${row.label} ${row.detail}" ${currentState.running?"disabled":""}><strong>${row.label}</strong><span>${row.detail}</span></button>${["read","write"].map(direction=>{
    const value=results[row.id]?.[direction]?.[currentUnit], running=active?.row===row.id && (active?.direction===direction || active?.direction==="mixed");
    return `<div class="metric-cell ${direction} ${running?"active":""}"><div class="metric-bar" style="width:${value===undefined?0:Math.min(100,value/maximum*100)}%"></div><span class="metric-value ${value===undefined?"empty":""}">${value===undefined?(running?t("测试中…"):"—"):metric(value)}</span>${value===undefined?"":`<small>${units[currentUnit]}</small>`}</div>`;
  }).join("")}</div>`).join("");
  document.querySelectorAll("[data-row]").forEach(button=>button.addEventListener("click",()=>guarded(button,()=>start(button.dataset.row))));
  $("result-hint").textContent=currentState.running?t("正在测量 · 进度按时长估算"):matches && Object.keys(results).length?($("profile").value==="qos"?t("混合成绩来自同一轮综合带宽峰值 · 展开逐轮结果查看波动"):t("实测结果 · 每项保留最佳轮次")):t("点击单项运行，或开始全部测试");
  $("export-current").disabled=!(matches && currentState.record_id && !currentState.running);
  renderQoS(results);
}
function renderQoS(results) {
  const rows=Object.values(results).flatMap(row=>["read","write"].flatMap(direction=>(row[direction]?.samples||[]).map((sample,index)=>`<tr><td>${escapeHTML(row.label)} ${escapeHTML(row.detail)} · ${index+1}</td><td>${direction==="read"?t("读取"):t("写入")}</td><td>${numeric(sample.mb_s,2)}</td>${["p50_us","p99_us","p999_us","p9999_us","p99999_us","max_latency_us"].map(key=>`<td>${numeric(sample[key],2)}</td>`).join("")}<td>${numeric(sample.total_ios)}${sample.tail_sample_warning?"<br><span class='sample-warning'>P99.99 尾部样本少</span>":""}</td></tr>`)));
  $("qos-rows").innerHTML=rows.join("")||"<tr><td colspan='10'>尚无有效测试结果。</td></tr>";
}
function dependencyUI(deps) {
  if(!deps) return;
  $("dependency-notice").classList.toggle("hidden",!!deps.fio?.available);
  for (const name of ["fio","smartctl"]) {const dep=deps[name]; if(!dep) continue; $(name+"-status").textContent=dep.available?"✓ "+name+" "+dep.version+" · "+dep.path:"○ "+(dep.error||t("未找到工具")); $(name+"-status").classList.toggle("available",dep.available);}
  $("result-engine").textContent=deps.fio?.available?"fio "+deps.fio.version:t("fio · 等待配置");
}
function applyState(state) {
  const previouslyRunning=currentState.running, oldPlot=currentState.plot_revision; currentState=state;
  $("connection-label").textContent=t("已连接本机"); $("connection-label").previousElementSibling.style.background="#37b587";
  $("platform-label").textContent=state.platform==="Darwin"?"macOS":state.platform;
  $("smart-admin-label").classList.toggle("hidden",state.platform!=="Darwin");
  dependencyUI(state.dependencies);
  const running=state.running, ready=state.dependencies?.fio?.available && $("target-path").value.trim();
  $("start-test").disabled=running || !ready;
  $("start-test").classList.toggle("hidden",running); $("stop-test").classList.toggle("hidden",!running);
  for(const id of ["target-path","pick-folder","profile","size","passes","duration","direct","engine","plot-mode","save-settings","refresh-dependencies"]) $(id).disabled=!!running;
  $("run-status").textContent=state.status==="idle"?t("就绪"):t(state.stage);
  $("run-subtitle").textContent=running?t("临时文件测试 · 可随时停止"):state.status==="completed"?t("结果已保存至测试记录"):state.status==="cancelled"?t("已完成的项目已保留"):state.status==="error"?t("查看运行日志了解详情"):t("选择目标目录后开始");
  $("run-progress").style.width=(state.progress||0)+"%"; $("run-percent").textContent=state.status==="idle"?"":Math.round(state.progress||0)+"%";
  $("run-error").classList.toggle("hidden",!state.error && !state.cleanup_warning); $("run-error").textContent=[state.error,state.cleanup_warning].filter(Boolean).join(" · ");
  const logs=state.logs||[]; $("log-count").textContent=t("{n} 条",{n:logs.length});
  $("log-content").innerHTML=logs.length?logs.map(item=>`<div class="log-line"><span class="log-time">${escapeHTML(item.time)}</span><span>${escapeHTML(t(item.message))}</span></div>`).join(""):t("尚无运行日志。");
  renderResults();
  if(location.hash==="#plots" && oldPlot!==state.plot_revision && window.Scatter) window.Scatter.load(state.plot_id).catch(error=>toast(t(error.message)));
  if(previouslyRunning && !running) {toast(state.status==="completed"?t("测试完成，成绩已保存。"):state.status==="cancelled"?t("测试已停止。"):t("测试失败，请查看日志。")); if(location.hash==="#history") loadHistory().catch(error=>toast(t(error.message)));}
}
async function start(row="all") {
  $("run-error").classList.add("hidden");
  try {const state=await api("start",runConfig(row)); storePreferences(); applyState(state);}
  catch(error) {$("run-error").textContent=t(error.message); $("run-error").classList.remove("hidden"); throw error;}
}
async function updatePath() {
  const path=$("target-path").value.trim();
  $("start-test").disabled=currentState.running || !currentState.dependencies?.fio?.available || !path;
  storePreferences(); renderResults(true);
  if(!path) {delete $("target-name").dataset.verbatim; delete $("target-description").dataset.verbatim;$("target-name").textContent=t("等待选择磁盘"); $("target-description").textContent=t("选择目标 SSD 中的测试目录"); return;}
  try {const info=await api("path-info",{path}); if(path!==$("target-path").value.trim()) return; $("target-path").value=info.path; storePreferences(); $("target-name").dataset.verbatim=""; $("target-description").dataset.verbatim=""; $("target-name").textContent=info.path.split(/[\\/]/).filter(Boolean).pop()||t("系统卷"); $("target-description").textContent=info.path; $("path-space").textContent=t("可用 {n}",{n:bytes(info.free)}); $("free-capacity").textContent=t("可用 {n}",{n:bytes(info.free)}); $("used-capacity").textContent=t("已用 {n}",{n:bytes(info.used)}); $("capacity-fill").style.width=(info.total?info.used/info.total*100:0)+"%";}
  catch(error) {$("path-space").textContent=t("目录不可用"); $("target-description").textContent=t(error.message);}
}
async function downloadReport(id,format="json") {
  const response=await fetch(`/api/export?id=${encodeURIComponent(id)}&format=${format}`,{headers:{"X-SSD-Token":token}});
  if(!response.ok) throw new Error((await response.json()).error);
  saveBlob(await response.blob(),`SSD-Lab-${id}.${format}`);
}
function saveBlob(blob,filename) {
  if(window.webkit?.messageHandlers?.saveFile) {
    const reader=new FileReader(); reader.onload=()=>window.webkit.messageHandlers.saveFile.postMessage({filename,data:reader.result.split(",")[1]}); reader.readAsDataURL(blob); return;
  }
  const link=document.createElement("a"), url=URL.createObjectURL(blob); link.href=url; link.download=filename; document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),3000);
}
function notice(message,isError=false) {$("smart-message").classList.remove("hidden"); $("smart-message").classList.toggle("error",isError); $("smart-message").textContent=message;}
async function scanSmart() {
  notice(t("正在扫描物理设备…"));
  try {const data=await api("smart/scan",{elevated:$("smart-admin").checked}); $("smart-device").innerHTML=data.devices.length?data.devices.map((device,index)=>`<option value="${escapeHTML(device.name)}" title="${escapeHTML(device.info)}">设备 ${index+1} · ${escapeHTML(device.protocol||device.type||"")} · ${escapeHTML(device.name.split("/").pop())}</option>`).join(""):"<option value=''>未发现可读取的设备</option>"; $("refresh-smart").disabled=!data.devices.length; if(data.devices.length) await readSmart(); else notice(data.warning||data.messages.join(" · "));}
  catch(error) {notice(t(error.message),true); throw error;}
}
async function readSmart() {
  if(!$("smart-device").value) return;
  notice(t("正在读取 SMART 信息…"));
  try {const data=await api("smart/read",{device:$("smart-device").value,elevated:$("smart-admin").checked,error_log:$("smart-error-log").checked}); currentSmart=data; renderSmart(data);}
  catch(error) {$("smart-content").classList.add("hidden"); notice(t(error.message),true); throw error;}
}
function renderSmart(data) {
  $("smart-content").classList.remove("hidden");
  const log=data.error_log||{status:"not_requested"};
  if(data.messages.length) notice(data.messages.map(message=>t(message)).join(" · "),data.partial||!data.health_data_available);
  else if(log.status==="failed") notice(t(data.health_data_available?"附加错误日志读取失败；SMART 健康数据已读出。仅凭此提示不能判定磁盘故障。":"附加错误日志读取失败；设备未提供健康数据。"),!data.health_data_available);
  else $("smart-message").classList.add("hidden");
  $("smart-diagnostics").classList.toggle("hidden",log.status==="not_requested");
  $("smart-log-status").textContent=log.status==="failed"?t("附加错误日志读取失败；详细返回信息如下。"):t("附加错误日志已读出；原始数据如下。");
  $("smart-log-raw").textContent=JSON.stringify(log.raw||{messages:log.messages},null,2);
  $("smart-updated").textContent=t("最近读取 {n}",{n:date(data.updated_at)});
  const label=$("health-label"); label.textContent={good:t("健康"),warning:t("需关注"),bad:t("异常"),unknown:t("未知")}[data.health]||t("未知"); label.className="health-label "+data.health;
  $("health-life").innerHTML=data.remaining===null?"—":`${numeric(data.remaining)}<small>%</small>`;
  $("health-detail").textContent=data.remaining===null?t("设备未报告可解释的剩余寿命"):t("NVMe 估算剩余寿命 · 非故障预测");
  $("smart-temperature").innerHTML=`${numeric(data.temperature)} <small>°C</small>`;
  $("temperature-fill").style.width=(data.temperature===null?0:Math.max(0,Math.min(100,data.temperature)))+"%";
  $("temperature-fill").style.background=data.temperature>=70?"#d49b63":"#7ab6a4";
  $("smart-model").dataset.verbatim=""; $("smart-model").textContent=data.model;
  if(!["Unknown model","未知型号"].includes(data.model) && $("smart-device").selectedOptions[0]) $("smart-device").selectedOptions[0].textContent=data.model+" · "+(data.protocol||"SMART");
  const nand=data.nand||{};
  const supplier=nand.supplier||(nand.status==="unavailable"?t("无法读取"):nand.status==="not_applicable"?t("不适用"):t("系统未公开"));
  const identity=[[t("序列号"),data.serial],[t("固件"),data.firmware],[t("接口"),data.protocol],[t("容量"),bytes(data.capacity)],[t("NAND 供应商"),supplier]];
  if(nand.reported_name)identity.push([t("供应商原始名称"),nand.reported_name],[t("信息来源"),nand.source]);
  if(nand.nand_model)identity.push([t("NAND 型号"),nand.nand_model]);
  identity.push([t("设备"),$("smart-device").value]);
  $("smart-identity").innerHTML=identity.map(([key,value])=>`<dt>${key}</dt><dd>${escapeHTML(value)}</dd>`).join("");
  $("smart-usage").innerHTML=[[t("通电时间"),numeric(data.power_hours),t("小时")],[t("通电次数"),numeric(data.power_cycles),t("次")],[t("累计主机写入"),bytes(data.write_bytes),t("NVMe 数据单元换算")],[t("累计主机读取"),bytes(data.read_bytes),t("NVMe 数据单元换算")]].map(([label,value,unit])=>`<div class="panel usage-card"><span>${label}</span><strong>${escapeHTML(value)}</strong><small>${unit}</small></div>`).join("");
  $("smart-attributes").innerHTML=data.attributes.length?data.attributes.map(item=>`<tr class="${item.status=== "warning"?"warning":""}"><td>${escapeHTML(item.id)}</td><td>${escapeHTML(t(item.name))}</td><td>${escapeHTML(item.value)}</td><td>${escapeHTML(item.worst)}</td><td>${escapeHTML(item.threshold)}</td><td>${escapeHTML(item.raw??item.unit??"—")}</td></tr>`).join(""):"<tr><td colspan='6'>设备未报告 NVMe / ATA 属性，请查看原始 JSON。</td></tr>";
  $("smart-raw").textContent=JSON.stringify(data.raw,null,2);
}
async function loadHistory() {
  const data=await api("history"); records=data.records;
  if(!records.length) {$("history-list").innerHTML='<div class="panel empty-state"><div class="empty-icon">◷</div><h3>还没有测试记录</h3><p>完成第一次测试后，成绩和测试条件会自动保存在本机。</p></div>'; return;}
  $("history-list").innerHTML=records.map(record=>{const peak=Math.max(0,...Object.values(record.results).map(row=>row.read?.mb_s||0)); return `<div class="panel history-card"><div class="history-icon">▥</div><div class="history-info"><h3><span data-verbatim>${escapeHTML(record.config.path)}</span><span class="status-chip ${record.status}">${{completed:t("完成"),cancelled:t("已停止"),error:t("失败")}[record.status]||record.status}</span></h3><p>${escapeHTML(date(record.created_at))} · ${({nvme:"NVMe",standard:t("标准"),qos:"QoS",posix:"POSIX"})[record.config.profile]||record.config.profile} · ${record.config.size_mib} MiB · ${t("{n} 轮",{n:record.config.passes})} · ${record.config.direct?"Direct I/O":t("缓存参与")}</p></div><div class="history-peaks">${peak.toFixed(2)}<small>读取峰值 MB/s</small></div><div class="history-actions"><button class="button secondary" data-plots="${record.id}">散点</button><button class="button secondary" data-history="${record.id}">查看</button><button class="button secondary" data-csv="${record.id}">CSV ↗</button><button class="button secondary" data-json="${record.id}">JSON ↗</button></div></div>`;}).join("");
  document.querySelectorAll("[data-plots]").forEach(button=>button.addEventListener("click",async()=>{await navigate("plots"); await window.Scatter.load(button.dataset.plots);}));
  document.querySelectorAll("[data-history]").forEach(button=>button.addEventListener("click",()=>showRecord(button.dataset.history)));
  for(const format of ["csv","json"]) document.querySelectorAll(`[data-${format}]`).forEach(button=>button.addEventListener("click",()=>guarded(button,()=>downloadReport(button.dataset[format],format))));
}
function showRecord(id) {
  selectedHistoryId=id;
  const record=records.find(item=>item.id===id); if(!record) return;
  $("history-detail").classList.remove("hidden"); $("history-detail").classList.add("panel","history-detail");
  $("history-detail").innerHTML=`<h3>${escapeHTML(date(record.created_at))} · ${t("测试详情")}</h3><p>${escapeHTML(record.config.path)}<br>fio ${escapeHTML(record.fio_version)} · ${escapeHTML(record.ioengine||"—")} · ${t("单项时长")} ${t("{n} 秒",{n:record.config.duration})} + ${t("预热 {n}",{n:record.methodology?.ramp_seconds??1})} · ${t("{n} 轮",{n:record.config.passes})} · ${record.config.size_mib} MiB<br>${t("准备数据")} ${bytes(record.preparation_bytes)} · ${t("已记录测试 I/O")} ${bytes(record.io_bytes)}${record.error?"<br>"+t("错误")+": "+escapeHTML(t(record.error)):""}${record.cleanup_warning?"<br>"+escapeHTML(record.cleanup_warning):""}</p><div class="table-scroll"><table><thead><tr><th>测试</th><th>方向</th><th>MB/s</th><th>IOPS</th><th>平均延迟 μs</th><th>有效轮次</th></tr></thead><tbody>${Object.values(record.results).flatMap(row=>["read","write"].filter(direction=>row[direction]).map(direction=>{const value=row[direction]; return `<tr><td>${escapeHTML(row.label)} ${escapeHTML(row.detail)}</td><td>${direction==="read"?t("读取"):t("写入")}</td><td>${value.mb_s.toFixed(2)}</td><td>${numeric(value.iops)}</td><td>${value.latency_us.toFixed(2)}</td><td>${value.samples.length}</td></tr>`;})).join("")||"<tr><td colspan='6'>没有已完成项目。</td></tr>"}</tbody></table></div>`;
  $("history-detail").scrollIntoView({behavior:"smooth",block:"nearest"});
}
async function poll() {
  if(pollBusy) return; pollBusy=true;
  try {const state=await api("state"); disconnected=false; if(JSON.stringify(state)!==JSON.stringify(currentState)) applyState(state);}
  catch(error) {if(!disconnected) toast(t(error.message)); disconnected=true; $("connection-label").textContent=t("连接已断开"); $("connection-label").previousElementSibling.style.background="#de946a"; $("start-test").disabled=true;}
  finally {pollBusy=false;}
}
window.addEventListener("languagechange",()=>{
  const page=location.hash.slice(1)||"benchmark";
  $("current-page-name").textContent=t(names[page]||names.benchmark);
  updateEstimate();if(currentState.platform)applyState(currentState);
  if(currentSmart)renderSmart(currentSmart);
  if(page==="history")loadHistory().then(()=>{if(selectedHistoryId)showRecord(selectedHistoryId);}).catch(error=>toast(t(error.message)));
  window.Scatter?.refreshLocale();
});
function events() {
  document.querySelectorAll("[data-page],[data-go]").forEach(button=>button.addEventListener("click",()=>navigate(button.dataset.page||button.dataset.go)));
  document.querySelectorAll("[data-unit]").forEach(button=>button.addEventListener("click",()=>{currentUnit=button.dataset.unit; document.querySelectorAll("[data-unit]").forEach(item=>item.classList.toggle("active",item===button)); renderResults(true);}));
  for(const id of ["profile","size","passes","duration","direct","engine","plot-mode"]) $(id).addEventListener("change",updateEstimate);
  $("engine").addEventListener("change",()=>{if(["psync","sync"].includes($("engine").value)) {$("profile").value="posix"; updateEstimate();}});
  $("target-path").addEventListener("input",()=>{clearTimeout(pathTimer); pathTimer=setTimeout(updatePath,500);});
  window.ssdFolderChosen=async(path)=>{$("target-path").value=path; await updatePath();};
  $("pick-folder").addEventListener("click",()=>guarded($("pick-folder"),async()=>{if(window.webkit?.messageHandlers?.chooseFolder) {window.webkit.messageHandlers.chooseFolder.postMessage({}); return;} const data=await api("pick-folder",{language:I18n.locale}); if(data.path) {$("target-path").value=data.path; await updatePath();}}));
  $("start-test").addEventListener("click",()=>guarded($("start-test"),()=>start()));
  $("stop-test").addEventListener("click",()=>guarded($("stop-test"),async()=>{await api("stop",{}); toast(t("正在停止测试并清理临时文件…"));}));
  $("export-current").addEventListener("click",()=>guarded($("export-current"),()=>downloadReport(currentState.record_id)));
  $("scan-smart").addEventListener("click",()=>guarded($("scan-smart"),scanSmart));
  $("refresh-smart").addEventListener("click",()=>guarded($("refresh-smart"),readSmart));
  $("smart-device").addEventListener("change",()=>guarded($("refresh-smart"),readSmart));
  $("export-smart").addEventListener("click",()=>{if(currentSmart) saveBlob(new Blob([JSON.stringify({smartctl:currentSmart.raw,ssd_lab:{nand:currentSmart.nand,error_log:currentSmart.error_log}},null,2)],{type:"application/json"}),"SSD-Lab-SMART.json");});
  $("refresh-history").addEventListener("click",()=>guarded($("refresh-history"),loadHistory));
  $("save-settings").addEventListener("click",()=>guarded($("save-settings"),async()=>{const data=await api("settings",{fio_path:$("fio-path").value.trim(),smartctl_path:$("smartctl-path").value.trim()}); dependencyUI(data.dependencies); toast(t("工具配置已保存。")+(!data.dependencies.fio.available?" fio 尚未就绪。":"")); await poll();}));
  $("refresh-dependencies").addEventListener("click",()=>guarded($("refresh-dependencies"),async()=>{const data=await api("dependencies/refresh",{}); dependencyUI(data.dependencies); await poll(); toast(t("工具检测完成。"));}));
  $("copy-install").addEventListener("click",async()=>{try {await navigator.clipboard.writeText("brew install fio smartmontools"); toast(t("安装命令已复制。"));} catch(_) {toast("安装命令：brew install fio smartmontools");}});
  window.addEventListener("hashchange",()=>navigate(location.hash.slice(1)));
}
async function init() {
  events(); loadPreferences(); updateEstimate(); await navigate(location.hash.slice(1)||"benchmark");
  try {const [state,settings]=await Promise.all([api("state"),api("settings")]); applyState(state); $("fio-path").value=settings.settings.fio_path||""; $("smartctl-path").value=settings.settings.smartctl_path||""; if($("target-path").value) await updatePath();} catch(error) {toast(t(error.message));}
  setInterval(poll,850);
}
init();
