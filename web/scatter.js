"use strict";
window.Scatter = (() => {
  const fields={time_s:"日志相对时间（s）",latency_us:"总 I/O 延迟（μs）",offset_mib:"测试文件偏移（MiB）",block_kib:"I/O 大小（KiB）",mb_s:"带宽（MB/s）",iops:"IOPS",queue_depth:"设定队列深度",threads:"线程数",pass:"轮次",p99_us:"P99 总延迟（μs）",p999_us:"P99.9 总延迟（μs）",p9999_us:"P99.99 总延迟（μs）"};
  let data={series:[]}, summary=[], identifier=null, requestNumber=0, rendered=[], chartPoints=[], scales=null;
  const canvas=$("scatter-canvas"), context=canvas.getContext("2d");
  function summaries(record) {
    return Object.values(record?.results||{}).flatMap(row=>["read","write"].flatMap(direction=>(row[direction]?.samples||[]).map((sample,index)=>{
      const match=row.detail.match(/Q(\d+)T(\d+)/);
      return {...sample,case:row.label+" "+row.detail,dir:direction,pass:index+1,queue_depth:match?Number(match[1]):null,threads:match?Number(match[2]):null};
    })));
  }
  function points() {
    const kind=$("plot-dataset").value;
    if(kind==="summary") return summary;
    return data.series.flatMap(series=>{
      const base={case:series.label,queue_depth:series.queue_depth,threads:series.threads,pass:series.pass,mode:series.mode};
      if(kind==="latency") return series.latency_points.map(point=>({...base,time_s:point[0],latency_us:point[1],dir:point[2]===0?"read":"write",offset_mib:point[3],block_kib:point[4],job:point[5]}));
      return series.flow_points.map(point=>({...base,time_s:point[0],mb_s:point[1],iops:point[2],dir:point[3]===0?"read":"write",job:point[4]}));
    });
  }
  function updateChoices() {
    const kind=$("plot-dataset").value;
    let options=kind==="latency"?["time_s","latency_us","queue_depth","threads","pass"]:kind==="flow"?["time_s","mb_s","iops","queue_depth","threads","pass"]:["pass","queue_depth","threads","mb_s","iops","latency_us","p99_us","p999_us","p9999_us"];
    if(kind==="latency" && data.series.some(series=>series.mode==="per_io")) options.splice(2,0,"offset_mib","block_kib");
    for(const axis of ["x","y"]) {
      const element=$("plot-"+axis), previous=element.value;
      element.innerHTML=options.map(key=>`<option value="${key}">${t(fields[key])}</option>`).join("");
      element.value=options.includes(previous)?previous:axis==="x"?(kind==="summary"?"queue_depth":"time_s"):(kind==="latency"?"latency_us":kind==="flow"?"mb_s":"iops");
    }
    const previous=$("plot-case").value;
    const cases=[...new Set(points().map(point=>point.case))];
    $("plot-case").innerHTML='<option value="all">全部项目</option>'+cases.map(name=>`<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("");
    $("plot-case").value=cases.includes(previous)?previous:"all";
    draw();
  }
  async function load(id) {
    const request=++requestNumber;
    try {
      const history=await api("history");
      const record=id?history.records.find(record=>record.id===id):history.records.find(record=>record.plot_available)||history.records[0];
      id=id||record?.id;
      if(!id) {data={series:[]}; summary=[]; identifier=null; updateChoices(); return;}
      const received=await api("plots?id="+encodeURIComponent(id));
      if(request!==requestNumber) return;
      identifier=id; data=received;
      summary=summaries(record||(currentState.plot_id===id?currentState:null));
      updateChoices();
    } catch(error) {if(request===requestNumber) toast(error.message);}
  }
  function bounds(values,axis,logarithmic) {
    let min=Infinity,max=-Infinity;
    for(const value of values) {min=Math.min(min,value);max=Math.max(max,value);}
    if(!values.length) {min=logarithmic?1:0;max=logarithmic?10:1;}
    if(min===max) {const padding=logarithmic?min/2:Math.max(1,Math.abs(min)*.1);min-=padding;max+=padding;}
    if(!logarithmic && min>=0) min=0;
    for(const edge of ["min","max"]) {
      const text=$("plot-"+axis+"-"+edge).value;
      if(text!=="" && Number.isFinite(Number(text))) {if(edge==="min") min=Number(text); else max=Number(text);}
    }
    if(min>=max || (logarithmic && min<=0)) return null;
    return [logarithmic?Math.log10(min):min,logarithmic?Math.log10(max):max];
  }
  function label(value) {if(Math.abs(value)>=10000 || (value>0 && value<.01)) return value.toExponential(1);return value.toLocaleString(I18n.locale,{maximumFractionDigits:2});}
  function draw() {
    const width=Math.max(400,canvas.parentElement.clientWidth), height=460, ratio=window.devicePixelRatio||1;
    canvas.width=width*ratio; canvas.height=height*ratio; canvas.style.width=width+"px"; canvas.style.height=height+"px";
    context.setTransform(ratio,0,0,ratio,0,0); const palette=getComputedStyle(document.documentElement),color=name=>palette.getPropertyValue(name).trim();context.fillStyle=color("--chart-bg");context.fillRect(0,0,width,height);
    const x=$("plot-x").value, y=$("plot-y").value, logX=$("plot-x-scale").value==="log", logY=$("plot-y-scale").value==="log";
    const direction=$("plot-direction").value, selected=$("plot-case").value;
    rendered=points().filter(point=>(direction==="all"||point.dir===direction)&&(selected==="all"||point.case===selected)&&typeof point[x]==="number"&&typeof point[y]==="number"&&Number.isFinite(point[x])&&Number.isFinite(point[y])&&(!logX||point[x]>0)&&(!logY||point[y]>0));
    const xBounds=bounds(rendered.map(point=>point[x]),"x",logX), yBounds=bounds(rendered.map(point=>point[y]),"y",logY);
    const left=88,right=width-30,top=52,bottom=height-62;
    context.font="12px -apple-system, sans-serif";context.fillStyle=color("--chart-ink");
    context.fillText("SSD Lab · "+t(fields[x])+" → "+t(fields[y]),left,26);
    context.font="10px -apple-system, sans-serif";
    context.fillStyle=color("--read");context.fillText("● "+t("读取"),width-145,43);
    context.fillStyle=color("--write");context.fillText("● "+t("写入"),width-80,43);
    if(!xBounds || !yBounds) {$("plot-count").textContent=t("{n} 个点",{n:0});$("export-plot-png").disabled=$("export-plot-csv").disabled=true;context.fillStyle=color("--warning");context.fillText(t("范围无效：最小值必须小于最大值，对数范围必须为正。"),left,120);chartPoints=[];return;}
    const sx=(value)=>left+((logX?Math.log10(value):value)-xBounds[0])/(xBounds[1]-xBounds[0])*(right-left);
    const sy=(value)=>bottom-((logY?Math.log10(value):value)-yBounds[0])/(yBounds[1]-yBounds[0])*(bottom-top);
    scales={sx,sy,x,y};
    context.font="10px ui-monospace, Menlo, monospace";context.strokeStyle=color("--chart-grid");context.lineWidth=1;
    for(let tick=0;tick<=5;tick++) {
      const tx=left+(right-left)*tick/5,ty=bottom-(bottom-top)*tick/5;
      context.beginPath();context.moveTo(tx,top);context.lineTo(tx,bottom);context.moveTo(left,ty);context.lineTo(right,ty);context.stroke();
      const xv=xBounds[0]+(xBounds[1]-xBounds[0])*tick/5,yv=yBounds[0]+(yBounds[1]-yBounds[0])*tick/5;
      context.fillStyle=color("--chart-ink");context.textAlign="center";context.fillText(label(logX?10**xv:xv),tx,bottom+20);
      context.textAlign="right";context.fillText(label(logY?10**yv:yv),left-12,ty+4);
    }
    context.textAlign="center";context.font="11px -apple-system, sans-serif";context.fillStyle=color("--chart-ink");context.fillText(t(fields[x]),(left+right)/2,height-13);
    context.save();context.translate(19,(top+bottom)/2);context.rotate(-Math.PI/2);context.fillText(t(fields[y]),0,0);context.restore();
    context.save();context.beginPath();context.rect(left,top,right-left,bottom-top);context.clip();chartPoints=[];
    for(const dir of ["read","write"]) {
      context.fillStyle=color(dir==="read"?"--read":"--write");context.globalAlpha=.55;context.beginPath();
      for(const point of rendered.filter(point=>point.dir===dir)) {const px=sx(point[x]),py=sy(point[y]);context.moveTo(px+2,py);context.arc(px,py,2,0,Math.PI*2);if(px>=left&&px<=right&&py>=top&&py<=bottom) chartPoints.push({px,py,point});}
      context.fill();
    }
    context.restore();context.globalAlpha=1;context.textAlign="left";
    if(!rendered.length) {context.fillStyle=color("--chart-ink");context.fillText(t("当前字段 / 筛选没有有效测量点"),left+40,top+80);}
    $("plot-count").textContent=t("{n} 个可见点 / {m} 个有效点",{n:chartPoints.length.toLocaleString(I18n.locale),m:rendered.length.toLocaleString(I18n.locale)});
    const modes=[...new Set(data.series.map(series=>series.mode))],rawRows=data.series.reduce((sum,series)=>sum+series.log_rows,0);
    const sampled=data.series.some(series=>series.sampling!=="all");
    $("plot-description").textContent=$("plot-dataset").value==="summary"?t("每个点是一个方向的一轮结果；设定 QD 不是实测瞬时队列深度。百分位来自完整 fio 统计，不从图中样本计算。"): [
      modes.includes("window")?t("窗口模式：延迟为 10 ms 窗口平均，带宽 / IOPS 日志按工作线程记录。"):"",
      modes.includes("per_io")?t("逐 I/O 模式：文件偏移可用；带宽 / IOPS 从已完成 I/O 计算为 100 ms 窗口。"):"",
      t("原始延迟日志 {n} 行。",{n:rawRows.toLocaleString(I18n.locale)}),
      t(sampled?"抽样显示并保留最慢点，不可用图中点数推算分布。":"当前记录保留全部有效延迟日志点。"),
      t("相对时间从每项第一个日志点开始；对数轴过滤非正值。")].filter(Boolean).join(" ");
    $("export-plot-png").disabled=$("export-plot-csv").disabled=!rendered.length;
  }
  for(const id of ["plot-dataset"]) $(id).addEventListener("change",updateChoices);
  for(const id of ["plot-case","plot-direction","plot-x","plot-y","plot-x-scale","plot-y-scale","plot-x-min","plot-x-max","plot-y-min","plot-y-max"]) $(id).addEventListener("change",draw);
  $("plot-reset").addEventListener("click",()=>{for(const axis of ["x","y"]) for(const edge of ["min","max"]) $("plot-"+axis+"-"+edge).value="";draw();});
  canvas.addEventListener("mousemove",event=>{
    const rectangle=canvas.getBoundingClientRect(),px=event.clientX-rectangle.left,py=event.clientY-rectangle.top;
    let nearest=null,distance=80;
    for(const item of chartPoints) {const squared=(item.px-px)**2+(item.py-py)**2;if(squared<distance){nearest=item;distance=squared;}}
    $("plot-tooltip").classList.toggle("hidden",!nearest);
    if(nearest) {$("plot-tooltip").textContent=`${nearest.point.case} · ${t("{n} 轮",{n:nearest.point.pass})} · ${t(nearest.point.dir==="read"?"读取":"写入")}\n${t(fields[scales.x])}: ${label(nearest.point[scales.x])}\n${t(fields[scales.y])}: ${label(nearest.point[scales.y])}`;$("plot-tooltip").style.left=Math.min(canvas.clientWidth-240,Math.max(10,px+12))+"px";$("plot-tooltip").style.top=Math.max(10,py-65)+"px";}
  });
  canvas.addEventListener("mouseleave",()=>$("plot-tooltip").classList.add("hidden"));
  $("export-plot-png").addEventListener("click",()=>canvas.toBlob(blob=>{if(blob) saveBlob(blob,"SSD-Lab-scatter-"+(identifier||"plot")+".png");},"image/png"));
  $("export-plot-csv").addEventListener("click",()=>{
    const x=$("plot-x").value,y=$("plot-y").value,quote=value=>'"'+String(value??"").replace(/"/g,'""')+'"';
    const csv=[['record_id','dataset','case','direction','pass',x,y].map(quote).join(","),...rendered.map(point=>[identifier,$("plot-dataset").value,point.case,point.dir,point.pass,point[x],point[y]].map(quote).join(","))].join("\r\n");
    saveBlob(new Blob(["\ufeff"+csv],{type:"text/csv;charset=utf-8"}),"SSD-Lab-scatter-"+(identifier||"plot")+".csv");
  });
  new ResizeObserver(()=>{if($("page-plots").classList.contains("active")) draw();}).observe(canvas.parentElement);
  updateChoices();
  window.addEventListener("appearancechange",draw);
  return {load,draw,refreshLocale:updateChoices};
})();
