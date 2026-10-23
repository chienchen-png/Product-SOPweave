import {Component,type ReactNode} from 'react';

/** A stale lazy chunk after an upgrade must leave a recovery action on screen. */
export default class PageBoundary extends Component<{children:ReactNode;onRetry?:()=>void},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(!this.state.failed)return this.props.children;
    return <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center"><h1 className="text-lg font-semibold">页面加载失败</h1><p className="text-sm text-zinc-500">页面资源可能已更新，请重新加载后重试。</p><button className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" onClick={this.props.onRetry||(()=>window.location.reload())}>重新加载页面</button></div>;
  }
}
