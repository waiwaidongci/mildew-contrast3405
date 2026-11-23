import {plot} from '../components/common/chart.js';
import {feedback} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {REVIEW, STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const PAIR_TABLE = '霉斑巡检成对核对';
const SHELF_TABLE = '架位配对进度';

function pairingBadge(result) {
  const info = result?.pairing;
  if (!info) return null;
  return pill(`已配对 ${info.pair_count} 对 / ${info.paired_record_count} 张`, 'complete');
}

export async function results(route, refresh) {
  const {spec,runs,datasets} = workspace;
  const id = route.id || runs[0]?.id;
  if (!id) return panel(spec.pages[3],empty('还没有可查看的运行结果。','#run','前往运行'));
  const job = await workspace.getRun(id);
  const source = datasets.find(item => item.id === job.dataset_id);
  const message = feedback();
  const finished = item => item.status === 'complete' || item.status === 'failed';
  if (!finished(job)) {
    const original = location.hash;
    const status = el('p',{role:'status'},'后台正在逐架位配对，全部配齐后才开始条件筛查，完成后自动展示。');
    const pairingLine = el('p',{class:'subtle','data-testid':'pairing-progress'},'正在读取配对进度…');
    const tick = async () => {
      const live = await workspace.getPairing(id);
      if (location.hash === original && pairingLine.isConnected) {
        pairingLine.textContent = live.completed_shelf_count
          ? `已配完 ${live.completed_shelf_count} 个架位，累计 ${live.pair_count} 对。中断后会从已完成架位续跑。`
          : '正在准备配对，配对不看湿度，同批运行共享同一组配对。';
      }
    };
    tick().catch(() => {});
    poll(()=>workspace.getRun(id),finished,()=>location.hash===original)
      .then(result=>{if (result && status.isConnected && location.hash===original) refresh();
        else if(status.isConnected) status.textContent='仍在执行，请点击刷新检查。';})
      .catch(error=>{if(status.isConnected) status.textContent=error.message;});
    return panel(job.name,pill(STATUS[job.status],job.status),status,pairingLine,
      button('刷新',{class:'quiet',onClick:refresh}));
  }
  const download = button('下载结果 CSV',{class:'quiet','data-testid':'export',onClick:event=>
    handler(download,message,()=>workspace.download(`/runs/${job.id}/export`,`${spec.short_name}-${job.id}.csv`))(event)});
  const retry = button('重新入队续跑',{class:'quiet','data-testid':'retry',onClick:event=>
    handler(retry,message,async()=>{await workspace.retry(job.id); await refresh();})(event)});
  const history = el('label',{class:'field run-picker',for:'run-picker'},el('span',{},'切换运行记录'),
    el('select',{id:'run-picker',onChange:event=>{location.hash=`#results/${event.target.value}`;}},
      (runs.some(item=>item.id===job.id) ? runs : [job,...runs]).map(item=>el('option',{value:item.id,selected:item.id===job.id},item.name))));
  const result = job.result;
  const pairPanel = result && panel('成对巡检比较',
    p('页面只比较成对巡检：同库房同架位、四天内一正一负，一张单只用一次。可在下表逐对核对单号。','intro-copy'),
    pairingBadge(result),
    ...result.tables.filter(item=>item.title===PAIR_TABLE || item.title===SHELF_TABLE)
      .map(item=>table(item.columns,item.rows,{caption:item.title})));
  const analysis = result ? el('section',{class:'analysis','data-testid':'analysis-complete'},
    el('div',{class:'result-head'},p(job.name,'eyeline'),h(2,result.headline),p(result.note,'intro-copy'),
      el('div',{class:'actions'},pill(STATUS[job.status],job.status),download,message)),
    metrics(result.metrics),
    result.charts.map(plot),
    pairPanel,
    (result.sections || []).map(item=>panel(item.title,p(item.text))),
    result.tables.filter(item=>item.title!==PAIR_TABLE && item.title!==SHELF_TABLE)
      .map(item=>panel(item.title,table(item.columns,item.rows,{caption:item.title}))))
    : panel(job.name,pill(STATUS[job.status],job.status),p(job.error,'feedback'),retry,
      link('返回调整参数',`#run/${job.dataset_id}`,{class:'quiet'}));
  const inputs = spec.params.map(param=>el('div',{},el('dt',{},param.label),el('dd',{},value(job.params[param.key]))));
  return el('div',{},el('div',{class:'two-col'},el('div',{class:'wide-panel'},analysis),
    el('aside',{class:'sidebar-panel'},panel('运行来源',history,
      source && link(source.name,`#inputs/${source.id}`),p(date(job.finished_at),'subtle'),
      el('dl',{class:'dictionary'},inputs)),
    result && panel(spec.decision, pill(REVIEW[job.review],job.review),
      link('打开复核记录',`#review/${job.id}`,{class:'quiet'})))));
}
